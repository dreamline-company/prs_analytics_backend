"""Переиспользуемое ядро загрузки телеметрии SDMO -> app-БД.

На уровне одной станции: прочитать новые строки источника keyset-пагинацией по
``(savetime, id)``, развернуть регистры JSON в широкую строку и COPY в
telemetry_sdmo_fc_data. Используется и bulk-, и инкрементальным скриптами;
отличается только оркестрация.

Источник append-only (строки только дописываются) → savetime растёт монотонно,
курсор ``(savetime, id)`` корректен, upsert не нужен, чистый COPY-append.

Почему keyset по ``(savetime, id)``, а не ``id``: у источника индекс
``trend(station_id, savetime, day)``, а индекса ``(station_id, id)`` нет.
С ORDER BY id MySQL идёт по PK и на каждый батч 50k строк одной станции
сканит десятки миллионов чужих строк. С ORDER BY savetime, id — попадаем в
``trend`` (``type: ref``, ~1.2M против 308M rows/EXPLAIN, ×250).
"""

import re
import time
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import Final

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.telemetry.dto.internal.repositories import (
    CreateSdmoFcRegDTO,
    CreateSdmoStationDTO,
)
from apps.telemetry.models.sdmo import SDMO_REGISTERS
from apps.telemetry.repositories import (
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.integrations.sdmo.models import FcDataDayParted
from shared.integrations.sdmo.repositories import (
    SDMOFcDataDayPartedRepository,
    SDMOFcRegRepository,
    SDMOStationRepository,
)
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)

ITER_BATCH_SIZE: Final = 100_000
# Как часто (в строках) логировать прогресс ВНУТРИ станции на уровне INFO.
PROGRESS_LOG_EVERY_ROWS: Final = 200_000

# Маппинг буквенного префикса имени скважины (до цифр): имя в SDMO -> wells_well.
# Напр. SDMO code "MLD_0177" соответствует Well.name "VMB_0177".
WELL_NAME_PREFIX_MAP: Final[dict[str, str]] = {
    "MLD": "VMB",
}
_WELL_NAME_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def map_well_name(code: str) -> str:
    """Заменить буквенный префикс имени скважины по WELL_NAME_PREFIX_MAP.

    Цифровая часть остаётся неизменной: "MLD_0177" -> "VMB_0177". Неизвестный
    префикс возвращается как есть.
    """
    match = _WELL_NAME_PREFIX_RE.match(code)
    if not match:
        return code
    mapped = WELL_NAME_PREFIX_MAP.get(match.group(1).upper())
    if mapped is None:
        return code
    return mapped + code[match.end() :]


def resolve_well_id(code: str | None, wells_ids: dict[str, int]) -> int | None:
    if not code:
        return None
    return wells_ids.get(map_well_name(code.strip()))


def merge_packets(data: dict | list | None) -> dict:
    """Свести JSON-пакет строки источника к плоскому словарю регистров.

    Станция шлёт регистры либо одним объектом, либо несколькими пакетами — во
    втором случае в ``data`` приезжает список объектов (тип поля источника это
    допускает: ``Mapped[dict | list | None]``). Пакеты сливаются в один словарь,
    поздний ключ перекрывает ранний. Без слияния ``.get`` по списку роняет
    загрузку станции целиком.
    """
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        merged: dict = {}
        for packet in data:
            if isinstance(packet, dict):
                merged.update(packet)
        return merged
    return {}


def reg_value(data: dict, addr: int) -> float | None:
    value = data.get(str(addr))
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_record(row: FcDataDayParted) -> tuple:
    """Развернуть строку-источник в широкий кортеж под FC_DATA_COPY_COLUMNS.

    Регистры из JSON `data` раскладываются по колонкам r_<addr> в порядке
    SDMO_REGISTERS; отсутствующие регистры → None.
    """
    data = merge_packets(row.data)
    return (
        row.id,
        row.station_id,
        row.day,
        row.savetime,
        *(reg_value(data, addr) for addr in SDMO_REGISTERS),
    )


async def _iter_source_station(
    sdmo_fc_repo: SDMOFcDataDayPartedRepository,
    station_id: int,
    last_savetime: datetime | None,
    last_id: int,
) -> AsyncGenerator[Sequence[FcDataDayParted]]:
    """Keyset-пагинация станции по ``(savetime, id)`` через индекс ``trend``.

    ``id`` в индекс не входит и служит только tiebreak'ом для одинакового
    savetime: ``(savetime, id) > (last_savetime, last_id)`` разворачивается в
    ``savetime > X OR (savetime = X AND id > Y)``. ``last_savetime = None``
    = грузим с самого начала (нет курсора).
    """
    cursor_savetime = last_savetime
    cursor_id = last_id
    while True:
        filters = [FcDataDayParted.station_id == station_id]
        if cursor_savetime is not None:
            filters.append(
                or_(
                    FcDataDayParted.savetime > cursor_savetime,
                    and_(
                        FcDataDayParted.savetime == cursor_savetime,
                        FcDataDayParted.id > cursor_id,
                    ),
                ),
            )
        rows = await sdmo_fc_repo.get_list(
            spec=QuerySpec(
                filters=tuple(filters),
                order_by=(
                    FcDataDayParted.savetime.asc(),
                    FcDataDayParted.id.asc(),
                ),
                limit=ITER_BATCH_SIZE,
            ),
        )
        if not rows:
            break
        yield rows
        cursor_savetime = rows[-1].savetime
        cursor_id = rows[-1].id
        if len(rows) < ITER_BATCH_SIZE:
            break


async def load_station_delta(
    app_session: AsyncSession,
    sdmo_session: AsyncSession,
    station_id: int,
    cursor: tuple[datetime | None, int],
    *,
    label: str = "",
) -> int:
    """Догрузить строки станции с ``(savetime, id) > cursor``.

    ``cursor = (None, 0)`` = станция ещё не грузилась. Коммитит по батчу
    (COPY + commit), так что прогресс сохраняется и загрузка резюмируема по
    курсору при падении. Логи: старт станции, первый батч (чтобы не выглядело
    зависшим на 5+ минут), далее каждые ``PROGRESS_LOG_EVERY_ROWS`` строк.
    ``label`` — префикс для контекста (напр. "[3/120] " или "[pid 1234] ").
    """
    app_repo = SdmoFcDataRepository(app_session)
    sdmo_fc_repo = SDMOFcDataDayPartedRepository(sdmo_session)
    last_savetime, last_id = cursor

    logger.info(
        "%sstation %s: start (cursor savetime=%s id=%s)",
        label,
        station_id,
        last_savetime,
        last_id,
    )

    started = time.monotonic()
    total = 0
    next_log = PROGRESS_LOG_EVERY_ROWS
    first_batch = True
    async for rows in _iter_source_station(
        sdmo_fc_repo,
        station_id,
        last_savetime,
        last_id,
    ):
        if first_batch:
            logger.info(
                "%sstation %s: first batch %s rows in %.1fs",
                label,
                station_id,
                len(rows),
                time.monotonic() - started,
            )
            first_batch = False
        await app_repo.copy_rows([to_record(row) for row in rows])
        await app_session.commit()
        total += len(rows)
        if total >= next_log:
            rate = total / max(time.monotonic() - started, 1e-9)
            logger.info(
                "%sstation %s: %s rows loaded (%.0f rows/s)",
                label,
                station_id,
                f"{total:,}",
                rate,
            )
            next_log += PROGRESS_LOG_EVERY_ROWS

    if total:
        rate = total / max(time.monotonic() - started, 1e-9)
        logger.info(
            "%sstation %s done: %s rows (%.0f rows/s)",
            label,
            station_id,
            f"{total:,}",
            rate,
        )
    else:
        logger.info("%sstation %s done: 0 rows (up to date)", label, station_id)
    return total


async def load_dimensions(
    app_session: AsyncSession,
    sdmo_session: AsyncSession,
) -> None:
    """Full-refresh справочников fc_reg и stations (мелкие, мутабельные).

    На станциях резолвит well_id из code (Station.code -> Well.name). fc_data
    ссылается на станции по sdmo_station_id (натуральный id), поэтому полная
    перезагрузка stations безопасна.
    """
    wells = await WellRepository(app_session).get_list()
    wells_ids = {well.name: well.id for well in wells}

    regs = await SDMOFcRegRepository(sdmo_session).get_list()
    reg_repo = SdmoFcRegRepository(app_session)
    await reg_repo.delete_all()
    await reg_repo.bulk_create(
        [
            CreateSdmoFcRegDTO(
                sdmo_id=reg.id,
                type_1900=reg.type_1900,
                addr=reg.addr,
                name=reg.name,
                units=reg.units,
                koef=reg.koef,
                type=reg.type,
                dynamic=reg.dynamic,
                info=reg.info,
                lora_bytes_size=reg.lora_bytes_size,
            )
            for reg in regs
        ],
    )
    await app_session.commit()
    logger.info("Loaded SDMO fc_reg: %s", len(regs))

    stations = await SDMOStationRepository(sdmo_session).get_list()
    station_repo = SdmoStationRepository(app_session)
    await station_repo.delete_all()
    await station_repo.bulk_create(
        [
            CreateSdmoStationDTO(
                sdmo_id=station.id,
                place_id=station.place_id,
                name=station.name,
                code=station.code,
                type_1900=station.type_1900,
                serial_number=station.serial_number,
                active=station.active,
                status=station.status,
                well_id=resolve_well_id(station.code, wells_ids),
            )
            for station in stations
        ],
    )
    await app_session.commit()
    logger.info("Loaded SDMO stations: %s", len(stations))


async def list_source_station_ids(
    sdmo_session: AsyncSession,
    station_ids: Sequence[int] | None = None,
) -> list[int]:
    """Список station_id для загрузки: явный список или все станции источника."""
    stations = await SDMOStationRepository(sdmo_session).get_list()
    all_ids = [station.id for station in stations]
    if station_ids is None:
        return all_ids
    wanted = set(station_ids)
    return [sid for sid in all_ids if sid in wanted]
