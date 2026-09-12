"""Постоянный опросчик замеров КБРС/Toucan.

Один долгоживущий процесс:
  * держит пул из ``KBRS_POOL_SIZE`` залогиненных Toucan-сессий;
  * фоновым keepalive-пингом не даёт простаивающим сессиям протухнуть
    (мёртвые перелогиниваются пулом автоматически);
  * каждые ``KBRS_POLL_INTERVAL_SECONDS`` секунд запрашивает список замеров
    по всем НГДУ из ``OWNERS_MAP`` в скользящем окне
    ``KBRS_POLL_WINDOW_HOURS`` часов;
  * новые замеры скачивает целиком; живые (``end_time`` ближе к now, чем
    ``KBRS_POLL_REFRESH_GRACE_MINUTES``) перечитывает — если сырой payload
    вырос, парсит и перезаливает заново;
  * сырой payload / chart.json / notes.json / passport.json кладёт в S3
    (ключи детерминированы — при перечитке объекты перезаписываются),
    метаданные — в таблицу ``kbrs_measure``.

Запуск:
    cd src && python -m apps.kbrs.tasks.poll_measures.poll_measures
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from io import BytesIO

from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.repositories.file import FileRepository
from apps.kbrs.dto.internal.repositories.measure import (
    CreateKbrsMeasureDTO,
    UpdateKbrsMeasureDTO,
)
from apps.kbrs.models.measure import (
    MEASURE_STATUS_OK,
    MEASURE_STATUS_PARSE_ERROR,
    KbrsMeasure,
)
from apps.kbrs.repositories.measure import KbrsMeasureRepository
from apps.repairs.tasks.fetch_sources.triggers import schedule_spo_link
from core import get_logger
from core.settings import get_settings
from shared.constants.kbrs import OWNERS_MAP
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers
from shared.integrations.kbrs.api import (
    DeviceDto,
    LoadMeasurementRequestDto,
    MeasureListFilterDto,
    MeasurementFullDto,
    MeasurementParsedDto,
    MeasurementPassportDto,
    MeasureRowDto,
    ToucanClientPool,
)
from shared.integrations.kbrs.api.enums import MeasureDateCondition
from shared.integrations.kbrs.api.exceptions import ToucanDecodeError
from shared.integrations.kbrs.api.parsers import (
    MeasurementFullParser,
    MeasurementPassportPeeker,
)

logger = get_logger(__name__)
settings = get_settings()


@dataclass(frozen=True, slots=True)
class _KnownMeasure:
    """Плоский снимок строки ``kbrs_measure``.

    Снимается до цикла фетчей, чтобы после commit/rollback не трогать
    просроченные ORM-объекты из ранее закрытой транзакции.
    """

    id: int
    raw_size: int
    status: str
    end_time: datetime | None
    fetched_at: datetime
    raw_file_id: int | None
    chart_json_file_id: int | None
    notes_file_id: int | None
    passport_file_id: int | None

    @classmethod
    def from_model(cls, row: KbrsMeasure) -> "_KnownMeasure":
        return cls(
            id=row.id,
            raw_size=row.raw_size,
            status=row.status,
            end_time=row.end_time,
            fetched_at=row.fetched_at,
            raw_file_id=row.raw_file_id,
            chart_json_file_id=row.chart_json_file_id,
            notes_file_id=row.notes_file_id,
            passport_file_id=row.passport_file_id,
        )


class KbrsMeasurePoller:
    """Держит Toucan-сессии и бесконечно опрашивает новые замеры."""

    def __init__(
        self,
        *,
        pool: ToucanClientPool,
        storage: AiobotoFileStorage,
        poll_interval_seconds: int,
        keepalive_interval_seconds: int,
        window_hours: int,
        refresh_grace_minutes: int,
        page_count: int,
    ) -> None:
        self._pool = pool
        self._storage = storage
        self._poll_interval = poll_interval_seconds
        self._keepalive_interval = keepalive_interval_seconds
        self._window_hours = window_hours
        self._refresh_grace = timedelta(minutes=refresh_grace_minutes)
        self._page_count = page_count
        self._stop_event = asyncio.Event()

    def request_stop(self) -> None:
        """Просит опросчик остановиться после текущей операции."""
        logger.info("Stop requested; finishing the current operation.")
        self._stop_event.set()

    async def run(self) -> None:
        logger.info(
            "KBRS poller started: pool=%s, interval=%ss, window=%sh, owners=%s.",
            self._pool.size,
            self._poll_interval,
            self._window_hours,
            list(OWNERS_MAP),
        )
        keepalive = asyncio.create_task(self._keepalive_loop())
        try:
            while not self._stop_event.is_set():
                cycle_started = time.monotonic()
                try:
                    await self._poll_once()
                except Exception:
                    logger.exception("Poll cycle failed; will retry on next tick.")
                await self._wait_next_tick(cycle_started)
        finally:
            keepalive.cancel()
            await asyncio.gather(keepalive, return_exceptions=True)
            logger.info("KBRS poller stopped.")

    async def _wait_next_tick(self, cycle_started: float) -> None:
        delay = max(0.0, self._poll_interval - (time.monotonic() - cycle_started))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)

    async def _keepalive_loop(self) -> None:
        """Пингует простаивающие сессии, чтобы сервер не закрыл их по таймауту."""
        ping_filters = MeasureListFilterDto(
            owner_id=next(iter(OWNERS_MAP)),
            condition_date=MeasureDateCondition.NOW,
            page_count=1,
        )
        while True:
            await asyncio.sleep(self._keepalive_interval)
            try:
                pinged = await self._pool.keepalive_all(
                    lambda client: client.list_measures(ping_filters),
                )
            except Exception:
                logger.exception("Keepalive pass failed.")
            else:
                logger.debug("Keepalive: pinged %s idle clients.", pinged)

    async def _poll_once(self) -> None:
        rows = await self._list_recent_measures()
        if not rows:
            logger.info("Poll: no measures in the window.")
            return

        async with session_makers["app"]() as session:
            measure_repo = KbrsMeasureRepository(session)
            file_repo = FileRepository(session)
            known = {
                measure_id: _KnownMeasure.from_model(model)
                for measure_id, model in (
                    await measure_repo.map_by_measure_ids(
                        [row.measure_id for row in rows],
                    )
                ).items()
            }
            now = _local_now()
            pending = [
                row for row in rows if self._needs_fetch(known.get(row.measure_id), now)
            ]
            logger.info(
                "Poll: %s measures in window, %s known, %s to fetch.",
                len(rows),
                len(known),
                len(pending),
            )

            descriptions = await self._device_descriptions(
                {row.owner_id for row in pending},
            )
            fetched = 0
            for row in pending:
                if self._stop_event.is_set():
                    break
                try:
                    linkable_well = await self._fetch_and_persist(
                        row,
                        existing=known.get(row.measure_id),
                        measure_repo=measure_repo,
                        file_repo=file_repo,
                        device_description=descriptions.get(
                            (int(row.owner_id), int(row.device_id)),
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Fetch failed for measure_id=%s; rolled back.",
                        row.measure_id,
                    )
                    await session.rollback()
                else:
                    await session.commit()
                    fetched += 1
                    if linkable_well is not None:
                        # Связывание с ремонтом — отдельная celery-таска:
                        # опросчик не ждёт S3 и парсинг чужого пайплайна.
                        schedule_spo_link(row.measure_id)
            if fetched:
                logger.info("Poll: fetched %s measures.", fetched)

    def _needs_fetch(self, existing: _KnownMeasure | None, now: datetime) -> bool:
        if existing is None:
            return True
        if existing.status != MEASURE_STATUS_OK or existing.end_time is None:
            # Битые/непарсящиеся — ретрай не чаще, чем раз в grace.
            return now - existing.fetched_at >= self._refresh_grace
        # Живой замер (мог дорасти), пока его правый край близок к now.
        return now - existing.end_time <= self._refresh_grace

    async def _list_recent_measures(self) -> list[MeasureRowDto]:
        now = _local_now()
        date_from = now - timedelta(hours=self._window_hours)
        date_to = now + timedelta(days=1)

        async def _for_owner(owner_id: int) -> list[MeasureRowDto]:
            filters = MeasureListFilterDto(
                owner_id=owner_id,
                condition_date=MeasureDateCondition.INTERVAL,
                date_from=date_from,
                date_to=date_to,
                page_count=self._page_count,
            )
            return await self._pool.call_with_retry(
                lambda client: client.list_measures(filters),
            )

        results = await asyncio.gather(
            *(_for_owner(owner_id) for owner_id in OWNERS_MAP),
            return_exceptions=True,
        )
        rows: dict[int, MeasureRowDto] = {}
        for owner_id, result in zip(OWNERS_MAP, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "list_measures failed for owner_id=%s: %r",
                    owner_id,
                    result,
                )
                continue
            for row in result:
                rows.setdefault(row.measure_id, row)
        return list(rows.values())

    async def _device_descriptions(
        self,
        owner_ids: set[int],
    ) -> dict[tuple[int, int], str]:
        """Описания приборов по (owner_id, device_id).

        Справочник приборов приходит вместе с логином в Toucan, ``list_devices``
        фильтрует его в памяти клиента — RPC здесь нет. Прибор, добавленный
        после логина сессии, появится после перелогина; до этого описание
        остаётся пустым и дозаполняется при следующем обновлении замера.
        """
        result: dict[tuple[int, int], str] = {}
        for owner_id in sorted(owner_ids):
            try:
                devices = await self._pool.call_with_retry(
                    lambda client, owner_id=owner_id: client.list_devices(
                        owner_id=owner_id,
                    ),
                )
            except Exception:  # noqa: BLE001 — описание не критично для замера
                logger.warning(
                    "list_devices failed for owner_id=%s; descriptions skipped.",
                    owner_id,
                    exc_info=True,
                )
                continue
            result.update(device_description_map(devices))
        return result

    async def _fetch_and_persist(
        self,
        row: MeasureRowDto,
        *,
        existing: _KnownMeasure | None,
        measure_repo: KbrsMeasureRepository,
        file_repo: FileRepository,
        device_description: str | None = None,
    ) -> int | None:
        """Скачать и сохранить замер.

        Возвращает номер скважины из паспорта, если содержимое замера
        изменилось и его можно связать с ремонтом; ``None`` — замер не вырос
        или скважина в паспорте не прочиталась.
        """
        # Только measure_id: с device_id/update_offset сервер отдаёт дельту
        # без паспорта в заголовке (см. KbrsSPOFetcher._probe_measure).
        request = LoadMeasurementRequestDto(measure_id=row.measure_id)
        raw = await self._pool.call_with_retry(
            lambda client: client.measurement_service.load_raw_measurement(request),
        )

        if existing is not None and len(raw) == existing.raw_size:
            # Не вырос с прошлого фетча — только отметить факт опроса.
            await measure_repo.update_by_id(
                existing.id,
                UpdateKbrsMeasureDTO(fetched_at=_local_now()),
            )
            return None

        well_number = MeasurementPassportPeeker.read_well(raw)
        full: MeasurementFullDto | None = None
        try:
            full = await asyncio.to_thread(MeasurementFullParser.parse, raw)
        except ToucanDecodeError as exc:
            logger.warning(
                "Measure %s: full parse failed (%s); raw stored as-is.",
                row.measure_id,
                exc,
            )

        prefix = f"kbrs/measures/{row.owner_id}/{row.device_id}/{row.measure_id}"
        raw_file_id = await self._upload(
            file_repo,
            payload=raw,
            s3_key=f"{prefix}/raw.bin",
            existing_file_id=existing.raw_file_id if existing else None,
        )

        values: dict = {
            "raw_size": len(raw),
            "status": MEASURE_STATUS_OK if full else MEASURE_STATUS_PARSE_ERROR,
            "raw_file_id": raw_file_id,
            "fetched_at": _local_now(),
        }
        if well_number is not None:
            values["well_number"] = well_number
        if device_description:
            values["device_description"] = device_description
        if full is not None:
            values["chart_json_file_id"] = await self._upload(
                file_repo,
                payload=_render_chart_json(full.chart),
                s3_key=f"{prefix}/chart.json",
                existing_file_id=existing.chart_json_file_id if existing else None,
            )
            values["notes_file_id"] = await self._upload(
                file_repo,
                payload=_render_notes(full.chart),
                s3_key=f"{prefix}/notes.json",
                existing_file_id=existing.notes_file_id if existing else None,
            )
            values["passport_file_id"] = await self._upload(
                file_repo,
                payload=_render_passport(full.details.passport),
                s3_key=f"{prefix}/passport.json",
                existing_file_id=existing.passport_file_id if existing else None,
            )
            values["start_time"] = _naive(full.chart.start)
            values["end_time"] = _naive(full.chart.end)
            values["row_count"] = len(full.chart.rows)

        if existing is None:
            await measure_repo.create(
                CreateKbrsMeasureDTO(
                    measure_id=row.measure_id,
                    owner_id=row.owner_id,
                    device_id=row.device_id,
                    device_type=row.device_type,
                    **values,
                ),
            )
        else:
            await measure_repo.update_by_id(
                existing.id,
                UpdateKbrsMeasureDTO(**values),
            )

        logger.info(
            "Measure %s (device=%s, well=%s): %s rows, %s bytes%s.",
            row.measure_id,
            row.device_id,
            well_number,
            len(full.chart.rows) if full else 0,
            len(raw),
            "" if full else " (parse error)",
        )
        return well_number

    async def _upload(
        self,
        file_repo: FileRepository,
        *,
        payload: bytes,
        s3_key: str,
        existing_file_id: int | None,
    ) -> int:
        """Кладёт payload в S3 и возвращает id строки ``files_file``.

        Ключи детерминированы: при перечитке замера объект в S3
        перезаписывается, а существующая строка файла переиспользуется.
        Ошибка загрузки пробрасывается — вызывающий цикл откатит транзакцию
        и повторит замер на следующем тике.
        """
        await self._storage.upload_file(BytesIO(payload), s3_key)
        if existing_file_id is not None:
            return existing_file_id
        file = await file_repo.create(CreateFileDTO(file=s3_key))
        return file.id


def device_description_map(
    devices: Sequence[DeviceDto],
) -> dict[tuple[int, int], str]:
    """(owner_id, device_id) → description; приборы с нечисловым id пропускаются."""
    result: dict[tuple[int, int], str] = {}
    for device in devices:
        try:
            device_id = int(device.device_id)
        except (TypeError, ValueError):
            continue
        description = (device.description or "").strip()
        if description:
            result[(int(device.owner_id), device_id)] = description
    return result


def _local_now() -> datetime:
    return datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _render_chart_json(parsed: MeasurementParsedDto) -> bytes:
    # Тот же компактный формат, что у KbrsSPOFetcher: ``[[ts_ms, value], ...]``
    # на канал — его нативно понимают ECharts / Highcharts / Chart.js.
    series_specs = (
        ("hook_weight_t", "т"),
        ("h2s_mg_m3", "мг/м³"),
        ("ch4_percent", "%"),
    )
    series = [
        {
            "key": key,
            "unit": unit,
            "points": [
                [row.timestamp * 1000, getattr(row, key)] for row in parsed.rows
            ],
        }
        for key, unit in series_specs
    ]
    payload = {
        "start": parsed.start.isoformat() if parsed.start else None,
        "end": parsed.end.isoformat() if parsed.end else None,
        "row_count": len(parsed.rows),
        "series": series,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _render_notes(parsed: MeasurementParsedDto) -> bytes:
    meta = {
        "magic": parsed.magic,
        "records_count": parsed.records_count,
        "row_count": len(parsed.rows),
        "channels": parsed.channels,
        "start": parsed.start.isoformat() if parsed.start else None,
        "end": parsed.end.isoformat() if parsed.end else None,
        "header_hint": parsed.header_ascii_hint,
    }
    return json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")


def _render_passport(passport: MeasurementPassportDto) -> bytes:
    # В ``values`` могут попадать datetime и другие не-JSON типы —
    # ``default=str`` сериализует их безопасно.
    return json.dumps(
        asdict(passport),
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")
