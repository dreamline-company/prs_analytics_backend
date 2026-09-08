"""Переиспользуемое ядро загрузки телеметрии SDMO -> app-БД.

На уровне одной станции: прочитать новые строки источника keyset-пагинацией по
``(savetime, id)``, развернуть регистры JSON в широкую строку и COPY в
telemetry_sdmo_fc_data. Используется и bulk-, и инкрементальным скриптами;
отличается только оркестрация.

Несколько НГДУ: у каждого своя база SDMO с одинаковой схемой и независимой
нумерацией (stations.id, fc_data.id начинаются с единицы в каждой). Ключ
станции во всей app-БД — локальный ``SdmoStation.id``; натуральный ``sdmo_id``
нужен только для обращения к источнику и уникален лишь внутри своего
``abai_ngdu_id``. Справочники обновляются upsert'ом и никогда не удаляются: на
``station.id`` ссылаются fc_data и курсоры/инциденты детекторов.

Источник append-only (строки только дописываются) → savetime растёт монотонно,
курсор ``(savetime, id)`` корректен, upsert не нужен, чистый COPY-append.

Почему keyset по ``(savetime, id)``, а не ``id``: у источника индекс
``trend(station_id, savetime, day)``, а индекса ``(station_id, id)`` нет.
С ORDER BY id MySQL идёт по PK и на каждый батч 50k строк одной станции
сканит десятки миллионов чужих строк. С ORDER BY savetime, id — попадаем в
``trend`` (``type: ref``, ~1.2M против 308M rows/EXPLAIN, ×250).
"""

import asyncio
import re
import time
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import Final

from sqlalchemy import and_, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.telemetry.dto.internal.repositories import (
    CreateSdmoFcRegDTO,
    CreateSdmoStationDTO,
)
from apps.telemetry.models.sdmo import SDMO_REGISTERS, SdmoStation
from apps.telemetry.repositories import (
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.wells.repositories import WellRepository
from apps.wells.repositories.well_org import WellOrgRepository
from apps.wells.services import NGDUWellsService
from core import get_logger
from shared.constants.ngdu import AbaiNGDUIDsEnum
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
# Обрыв соединения с MySQL источника (2013 «Lost connection», 2006 «gone
# away») под нагрузкой — штатная ситуация, а не ошибка данных: батч
# повторяется от последнего закоммиченного курсора с растущей паузой.
SOURCE_RETRIES: Final = 5
SOURCE_RETRY_BACKOFF_SEC: Final = 15
# Сколько кодов «чужих» станций показывать в предупреждении.
FOREIGN_CODES_IN_LOG: Final = 10

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


def to_record(row: FcDataDayParted, station: SdmoStation) -> tuple:
    """Развернуть строку-источник в широкий кортеж под FC_DATA_COPY_COLUMNS.

    В строку идут локальный ``station.id`` и ``station.abai_ngdu_id`` — не
    ``row.station_id`` источника: тот уникален только внутри базы своего НГДУ.
    Регистры из JSON `data` раскладываются по колонкам r_<addr> в порядке
    SDMO_REGISTERS; отсутствующие регистры → None.
    """
    data = merge_packets(row.data)
    return (
        row.id,
        station.id,
        station.abai_ngdu_id,
        row.day,
        row.savetime,
        *(reg_value(data, addr) for addr in SDMO_REGISTERS),
    )


def select_stations(
    stations: Sequence[SdmoStation],
    sdmo_ids: Sequence[int] | None,
) -> list[SdmoStation]:
    """Сузить станции НГДУ до явного списка натуральных ``sdmo_id`` (если задан)."""
    if sdmo_ids is None:
        return list(stations)
    wanted = set(sdmo_ids)
    return [station for station in stations if station.sdmo_id in wanted]


async def _iter_source_station(
    sdmo_fc_repo: SDMOFcDataDayPartedRepository,
    station_id: int,
    last_savetime: datetime | None,
    last_id: int,
) -> AsyncGenerator[Sequence[FcDataDayParted]]:
    """Keyset-пагинация станции по ``(savetime, id)`` через индекс ``trend``.

    ``station_id`` здесь — натуральный id источника. ``id`` в индекс не входит
    и служит только tiebreak'ом для одинакового savetime: ``(savetime, id) >
    (last_savetime, last_id)`` разворачивается в ``savetime > X OR (savetime = X
    AND id > Y)``. ``last_savetime = None`` = грузим с самого начала (нет курсора).
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
    station: SdmoStation,
    cursor: tuple[datetime | None, int],
    *,
    label: str = "",
) -> int:
    """Догрузить строки станции с ``(savetime, id) > cursor``.

    Источник опрашивается по натуральному ``station.sdmo_id``, в app-БД строки
    ложатся под локальным ``station.id``. ``cursor = (None, 0)`` = станция ещё
    не грузилась. Коммитит по батчу (COPY + commit), так что прогресс
    сохраняется и загрузка резюмируема по курсору при падении. Логи: старт
    станции, первый батч (чтобы не выглядело зависшим на 5+ минут), далее
    каждые ``PROGRESS_LOG_EVERY_ROWS`` строк. ``label`` — префикс для
    контекста (напр. "[KMG 3/120] " или "[pid 1234] ").
    """
    app_repo = SdmoFcDataRepository(app_session)
    sdmo_fc_repo = SDMOFcDataDayPartedRepository(sdmo_session)
    last_savetime, last_id = cursor
    # В логах — натуральный id: его видно в самой SDMO.
    station_ref = f"station {station.sdmo_id} (id={station.id})"

    logger.info(
        "%s%s: start (cursor savetime=%s id=%s)",
        label,
        station_ref,
        last_savetime,
        last_id,
    )

    started = time.monotonic()
    total = 0
    next_log = PROGRESS_LOG_EVERY_ROWS
    first_batch = True
    attempt = 0
    while True:
        try:
            async for rows in _iter_source_station(
                sdmo_fc_repo,
                station.sdmo_id,
                last_savetime,
                last_id,
            ):
                if first_batch:
                    logger.info(
                        "%s%s: first batch %s rows in %.1fs",
                        label,
                        station_ref,
                        len(rows),
                        time.monotonic() - started,
                    )
                    first_batch = False
                await app_repo.copy_rows([to_record(row, station) for row in rows])
                await app_session.commit()
                total += len(rows)
                last_savetime, last_id = rows[-1].savetime, rows[-1].id
                if total >= next_log:
                    rate = total / max(time.monotonic() - started, 1e-9)
                    logger.info(
                        "%s%s: %s rows loaded (%.0f rows/s)",
                        label,
                        station_ref,
                        f"{total:,}",
                        rate,
                    )
                    next_log += PROGRESS_LOG_EVERY_ROWS
            break
        except OperationalError as exc:
            attempt += 1
            if attempt > SOURCE_RETRIES:
                raise
            # Сбросить обе сессии: у источника соединение мёртвое, пул выдаст
            # новое; у app-БД незакоммиченного нет, но транзакция могла остаться
            # открытой. Курсор — последний закоммиченный батч.
            await sdmo_session.rollback()
            await app_session.rollback()
            pause = SOURCE_RETRY_BACKOFF_SEC * attempt
            logger.warning(
                "%s%s: source connection lost (%s); retry %s/%s in %ss from "
                "cursor savetime=%s id=%s",
                label,
                station_ref,
                type(exc.orig).__name__ if exc.orig else type(exc).__name__,
                attempt,
                SOURCE_RETRIES,
                pause,
                last_savetime,
                last_id,
            )
            await asyncio.sleep(pause)

    if total:
        rate = total / max(time.monotonic() - started, 1e-9)
        logger.info(
            "%s%s done: %s rows (%.0f rows/s)",
            label,
            station_ref,
            f"{total:,}",
            rate,
        )
    else:
        logger.info("%s%s done: 0 rows (up to date)", label, station_ref)
    return total


async def _well_ids_by_name(
    app_session: AsyncSession,
    ngdu: AbaiNGDUIDsEnum,
) -> tuple[dict[str, int], dict[str, int]]:
    """``(скважины НГДУ, все скважины)`` как ``имя -> well_id``.

    Станция привязывается только к скважине своего НГДУ: код станции одного
    НГДУ не должен случайно совпасть со скважиной другого. Если зеркало
    оргструктуры для НГДУ пусто (привязки ещё не загружены), ограничение
    снимается с предупреждением — лучше привязать по имени, чем оставить весь
    НГДУ без скважин.
    """
    all_wells = await WellRepository(app_session).get_list()
    all_ids = {well.name: well.id for well in all_wells}

    org_repository = OrgRepository(app_session)
    org = await org_repository.get_by_abai_id(int(ngdu))
    ngdu_wells = (
        await NGDUWellsService(
            org_repository=org_repository,
            well_repository=WellRepository(app_session),
            well_org_repository=WellOrgRepository(app_session),
            oil_field_repository=OilFieldRepository(app_session),
        ).list_wells(org.id)
        if org is not None
        else []
    )
    if not ngdu_wells:
        logger.warning(
            "NGDU %s has no wells in org mirror; stations are linked by name "
            "across all wells",
            ngdu.name,
        )
        return all_ids, all_ids
    return {well.name: well.id for well in ngdu_wells}, all_ids


async def load_dimensions(
    app_session: AsyncSession,
    sdmo_session: AsyncSession,
    ngdu: AbaiNGDUIDsEnum,
) -> list[SdmoStation]:
    """Обновить справочники fc_reg (общий) и stations (своего НГДУ) upsert'ом.

    Возвращает станции НГДУ из app-БД (с локальными id) — с ними работают
    загрузка и детекторы. Справочник регистров один на все НГДУ с ключом
    (type_1900, addr): базы SDMO расходятся по натуральным id регистров, а
    новые адреса из очередной базы просто добавляются. Станции
    получают ``well_id`` по коду (Station.code -> Well.name) среди скважин
    своего НГДУ; совпадения с чужими скважинами не привязываются и попадают
    в лог.
    """
    regs = await SDMOFcRegRepository(sdmo_session).get_list()
    await SdmoFcRegRepository(app_session).upsert_many(
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
    logger.info("[%s] SDMO fc_reg upserted: %s", ngdu.name, len(regs))
    # Широкая таблица fc_data имеет колонку только под адреса из
    # SDMO_REGISTERS; значения остальных регистров источника не сохраняются.
    unknown_addrs = sorted({reg.addr for reg in regs} - set(SDMO_REGISTERS))
    if unknown_addrs:
        logger.warning(
            "[%s] %s register addrs have no r_<addr> column and are not stored: %s",
            ngdu.name,
            len(unknown_addrs),
            ", ".join(map(str, unknown_addrs)),
        )

    ngdu_wells, all_wells = await _well_ids_by_name(app_session, ngdu)
    source_stations = await SDMOStationRepository(sdmo_session).get_list()
    foreign_codes: list[str] = []
    dtos: list[CreateSdmoStationDTO] = []
    for station in source_stations:
        well_id = resolve_well_id(station.code, ngdu_wells)
        if well_id is None and resolve_well_id(station.code, all_wells) is not None:
            foreign_codes.append(station.code or "")
        dtos.append(
            CreateSdmoStationDTO(
                abai_ngdu_id=int(ngdu),
                sdmo_id=station.id,
                place_id=station.place_id,
                name=station.name,
                code=station.code,
                type_1900=station.type_1900,
                serial_number=station.serial_number,
                active=station.active,
                status=station.status,
                well_id=well_id,
            ),
        )
    station_repo = SdmoStationRepository(app_session)
    await station_repo.upsert_many(dtos)
    await app_session.commit()
    if foreign_codes:
        logger.warning(
            "[%s] %s stations match wells of other NGDUs and stay unlinked: %s",
            ngdu.name,
            len(foreign_codes),
            ", ".join(foreign_codes[:FOREIGN_CODES_IN_LOG]),
        )

    stations = list(await station_repo.list_by_ngdu(int(ngdu)))
    linked = sum(1 for station in stations if station.well_id is not None)
    logger.info(
        "[%s] SDMO stations upserted: %s from source, %s in mirror, %s linked to wells",
        ngdu.name,
        len(source_stations),
        len(stations),
        linked,
    )
    return stations
