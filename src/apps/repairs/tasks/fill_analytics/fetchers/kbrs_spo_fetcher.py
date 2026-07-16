"""Fetches SPO (спуско-подъёмные операции) data from kbrs / Toucan.

NGDU-centric routing:
  1. Resolve the well's NGDU via ``GetNGDUForWellUseCase`` (walks
     ``well_org → org.parent_id → …`` until an NGDU-typed org).
  2. Map the NGDU's ABAI id to a kbrs ``owner_id`` through
     ``shared.constants.kbrs.OWNERS_MAP``.
  3. ``list_devices(owner_id)`` — all devices of that NGDU.
  4. For each device (in parallel), list measurements in the repair window
     (``[start_time, end_time+1d]``); for each measurement fetch the raw
     payload and peek at ``passport.well`` (fixed offset 0x49 in the DEL-150
     header) — no full parsing until a match is found.
  5. On first per-device match: parse the already-in-hand raw once, persist
     as one ``SPO`` row plus its event set (``repairs_spo_event``, replaced
     on re-fetch). No second RPC.

Parallelism is bounded by ``ToucanClientPool`` size — each in-flight RPC
holds a client checked out from the pool.

Each stored SPO row carries:
  * ``file_id``       — raw binary measurement payload (master file).
  * ``chart_file_id`` — CSV export of the chart.
  * ``notes_file_id`` — JSON metadata blob (channels, time range, event count).
"""

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from io import BytesIO

from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.models.file import File
from apps.files.repositories.file import FileRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.repairs.models.repair import Repair
from apps.wells.dto.internal.repositories.spo import CreateSPODTO, UpdateSPODTO
from apps.wells.dto.internal.repositories.spo_event import CreateSPOEventDTO
from apps.wells.models.spo import SPO
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.spo_event import SPOEventRepository
from core import get_logger
from shared.constants.kbrs import OWNERS_MAP
from shared.database.s3.storage import AiobotoFileStorage
from shared.integrations.kbrs.api import ToucanClientPool
from shared.integrations.kbrs.api.dtos import (
    DeviceDto,
    LoadMeasurementRequestDto,
    MeasureListFilterDto,
    MeasurementFullDto,
    MeasurementPassportDto,
    MeasureRowDto,
)
from shared.integrations.kbrs.api.enums import MeasureDateCondition
from shared.integrations.kbrs.api.exceptions import ToucanDecodeError
from shared.integrations.kbrs.api.parsers import (
    MeasurementFullParser,
    MeasurementPassportPeeker,
)

logger = get_logger(__name__)


_WELL_NUMBER_RE = re.compile(r"(\d+)")


def extract_well_number(well_name: str) -> int | None:
    """Extract the trailing well number from a name like ``VMB_0177``.

    Picks the last group of digits and strips leading zeros. Returns
    ``None`` if no digits are present.
    """
    matches = _WELL_NUMBER_RE.findall(well_name)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _owner_id_by_ngdu_abai_id(ngdu_abai_id: int) -> int | None:
    """Reverse-lookup of ``OWNERS_MAP``: ABAI NGDU id → kbrs owner_id."""
    for owner_id, meta in OWNERS_MAP.items():
        if int(meta["owner_abai_id"]) == int(ngdu_abai_id):
            return owner_id
    return None


class KbrsSPOFetcher:
    def __init__(  # noqa: PLR0913
        self,
        pool: ToucanClientPool,
        storage: AiobotoFileStorage,
        file_repo: FileRepository,
        spo_repo: SPORepository,
        spo_event_repo: SPOEventRepository,
        get_ngdu_for_well: GetNGDUForWellUseCase,
    ) -> None:
        self._pool = pool
        self._storage = storage
        self._file_repo = file_repo
        self._spo_repo = spo_repo
        self._spo_event_repo = spo_event_repo
        self._get_ngdu_for_well = get_ngdu_for_well

    async def fetch_for_repair(
        self,
        repair: Repair,
        *,
        well_id: int,
        well_name: str,
        abai_well_id: int,
    ) -> list[SPO]:
        target_well_number = extract_well_number(well_name)
        if target_well_number is None:
            logger.warning(
                "SPO skipped for repair id=%s — cannot parse well number "
                "from well.name=%r.",
                repair.id,
                well_name,
            )
            return []

        ngdu = await self._get_ngdu_for_well.execute(abai_well_id)
        if ngdu is None:
            logger.warning(
                "SPO skipped for repair id=%s — no NGDU resolved for abai_well_id=%s.",
                repair.id,
                abai_well_id,
            )
            return []

        owner_id = _owner_id_by_ngdu_abai_id(ngdu.abai_id)
        if owner_id is None:
            logger.warning(
                "SPO skipped for repair id=%s — NGDU abai_id=%s (%s) is not "
                "in kbrs OWNERS_MAP.",
                repair.id,
                ngdu.abai_id,
                ngdu.name_ru,
            )
            return []

        logger.info(
            "Repair id=%s: NGDU=%s (abai_id=%s) → kbrs owner_id=%s, "
            "target well number=%s (from %r).",
            repair.id,
            ngdu.name_ru,
            ngdu.abai_id,
            owner_id,
            target_well_number,
            well_name,
        )

        devices: Sequence[DeviceDto] = await self._pool.call_with_retry(
            lambda client: client.list_devices(owner_id=owner_id),
        )
        logger.info(
            "Repair id=%s: kbrs list_devices(owner_id=%s) → %s devices.",
            repair.id,
            owner_id,
            len(devices),
        )
        if not devices:
            return []

        end = repair.end_time or datetime.now()  # noqa: DTZ005

        device_tasks = [
            asyncio.create_task(
                self._fetch_for_device(
                    repair=repair,
                    well_id=well_id,
                    target_well_number=target_well_number,
                    owner_id=owner_id,
                    device=device,
                    start=repair.start_time,
                    end=end,
                ),
            )
            for device in devices
        ]
        results = await asyncio.gather(*device_tasks, return_exceptions=True)

        spos: list[SPO] = []
        for device, result in zip(devices, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "Repair id=%s device_id=%s fetch failed: %r",
                    repair.id,
                    device.device_id,
                    result,
                )
                continue
            if result is not None:
                spos.append(result)
        return spos

    async def _fetch_for_device(  # noqa: PLR0913
        self,
        *,
        repair: Repair,
        well_id: int,
        target_well_number: int,
        owner_id: int,
        device: DeviceDto,
        start: datetime,
        end: datetime,
    ) -> SPO | None:
        try:
            device_id_int = int(device.device_id)
        except (TypeError, ValueError):
            logger.debug(
                "Device id=%r is not an int; skipped (repair id=%s).",
                device.device_id,
                repair.id,
            )
            return None

        measures = await self._pool.call_with_retry(
            lambda client: client.list_measures(
                MeasureListFilterDto(
                    owner_id=owner_id,
                    device_id=str(device_id_int),
                    condition_date=MeasureDateCondition.INTERVAL,
                    date_from=start,
                    date_to=end + timedelta(days=1),
                    page_count=50,
                ),
            ),
        )
        if not measures:
            return None

        matched = await self._first_matching_measure(
            measures=measures,
            target_well_number=target_well_number,
        )
        if matched is None:
            return None

        measure, raw = matched
        try:
            full = await asyncio.to_thread(MeasurementFullParser.parse, raw)
        except ToucanDecodeError as exc:
            logger.warning(
                "Repair id=%s device_id=%s measure_id=%s: matched by passport "
                "but full parse failed (%s); skipping persist.",
                repair.id,
                device.device_id,
                measure.measure_id,
                exc,
            )
            return None

        logger.info(
            "Repair id=%s: matched device_id=%s measure_id=%s "
            "(passport.well=%s == %s).",
            repair.id,
            device.device_id,
            measure.measure_id,
            target_well_number,
            target_well_number,
        )

        return await self._persist_measurement(
            repair=repair,
            well_id=well_id,
            measure=measure,
            raw_bytes=raw,
            full=full,
        )

    async def _first_matching_measure(
        self,
        *,
        measures: Sequence[MeasureRowDto],
        target_well_number: int,
    ) -> tuple[MeasureRowDto, bytes] | None:
        """Probe measures in parallel; return the first passport match.

        Uses the pool for RPC (each probe grabs a client for one RPC).
        As soon as any probe returns a match, the rest are cancelled.
        """

        tasks = [
            asyncio.create_task(
                self._probe_measure(measure, target_well_number),
            )
            for measure in measures
        ]
        matched: tuple[MeasureRowDto, bytes] | None = None
        try:
            for coro in asyncio.as_completed(tasks):
                try:
                    result = await coro
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Measure probe error: %r", exc)
                    continue
                if result is not None:
                    matched = result
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return matched

    async def _probe_measure(
        self,
        measure: MeasureRowDto,
        target_well_number: int,
    ) -> tuple[MeasureRowDto, bytes] | None:
        # Only measure_id: passing device_id / update_offset makes the server
        # return a different payload (delta/partial) without the passport
        # header at offset 0x49. The original _load_full used the same
        # single-param form; draft_kbrs.py explicitly comments the extras out.
        request = LoadMeasurementRequestDto(measure_id=measure.measure_id)
        raw = await self._pool.call_with_retry(
            lambda client: client.measurement_service.load_raw_measurement(request),
        )
        well = MeasurementPassportPeeker.read_well(raw)
        if well is None or well != target_well_number:
            return None
        return measure, raw

    async def _persist_measurement(
        self,
        *,
        repair: Repair,
        well_id: int,
        measure: MeasureRowDto,
        raw_bytes: bytes,
        full: MeasurementFullDto,
    ) -> SPO | None:
        parsed = full.chart
        events = full.details.events

        snapshot_time = parsed.start or repair.start_time
        if snapshot_time is not None and snapshot_time.tzinfo is not None:
            snapshot_time = snapshot_time.replace(tzinfo=None)

        existing = await self._spo_repo.get_by_well_id_and_snapshot_time(
            well_id=well_id,
            snapshot_time=snapshot_time,
        )
        if (
            existing is not None
            and existing.chart_file_id
            and existing.chart_json_file_id
            and existing.notes_file_id
            and existing.passport_file_id
        ):
            await self._persist_events(existing.id, events)
            return existing

        csv_bytes = self._render_csv(parsed)
        chart_json_bytes = self._render_chart_json(parsed)
        notes_bytes = self._render_notes(parsed)
        passport_bytes = self._render_passport(full.details.passport)

        prefix = f"spo/{well_id}/{measure.measure_id}"
        master_file = await self._upload_and_register(
            payload=raw_bytes,
            s3_key=f"{prefix}/raw.bin",
        )
        chart_file = await self._upload_and_register(
            payload=csv_bytes,
            s3_key=f"{prefix}/chart.csv",
        )
        chart_json_file = await self._upload_and_register(
            payload=chart_json_bytes,
            s3_key=f"{prefix}/chart.json",
        )
        notes_file = await self._upload_and_register(
            payload=notes_bytes,
            s3_key=f"{prefix}/notes.json",
        )
        passport_file = await self._upload_and_register(
            payload=passport_bytes,
            s3_key=f"{prefix}/passport.json",
        )

        if master_file is None:
            return None

        if existing is None:
            spo = await self._spo_repo.create(
                CreateSPODTO(
                    file_id=master_file.id,
                    chart_file_id=chart_file.id if chart_file else None,
                    chart_json_file_id=chart_json_file.id if chart_json_file else None,
                    notes_file_id=notes_file.id if notes_file else None,
                    passport_file_id=passport_file.id if passport_file else None,
                    snapshot_time=snapshot_time,
                    well_id=well_id,
                ),
            )
        else:
            spo = await self._spo_repo.update(
                data=UpdateSPODTO(
                    file_id=master_file.id,
                    chart_file_id=chart_file.id if chart_file else None,
                    chart_json_file_id=chart_json_file.id if chart_json_file else None,
                    notes_file_id=notes_file.id if notes_file else None,
                    passport_file_id=passport_file.id if passport_file else None,
                ),
                filters=(SPO.id == existing.id,),
            )

        await self._persist_events(spo.id, events)
        return spo

    async def _persist_events(
        self,
        spo_id: int,
        events: Sequence,
    ) -> None:
        dtos = [
            CreateSPOEventDTO(
                spo_id=spo_id,
                offset=event.offset,
                time_text=event.time_text,
                code=event.code,
                text=event.text,
                raw_text=event.raw_text,
            )
            for event in events
        ]
        await self._spo_event_repo.replace_for_spo(spo_id, dtos)
        logger.info(
            "Persisted %s SPO events for spo_id=%s.",
            len(dtos),
            spo_id,
        )

    @staticmethod
    def _render_csv(parsed) -> bytes:  # noqa: ANN001
        header = "timestamp,datetime,hook_weight_t,h2s_mg_m3,ch4_percent\n"
        lines = [
            header,
            *(
                f"{row.timestamp},"
                f"{row.datetime.isoformat() if row.datetime else ''},"
                f"{row.hook_weight_t if row.hook_weight_t is not None else ''},"
                f"{row.h2s_mg_m3 if row.h2s_mg_m3 is not None else ''},"
                f"{row.ch4_percent if row.ch4_percent is not None else ''}\n"
                for row in parsed.rows
            ),
        ]
        return "".join(lines).encode("utf-8")

    @staticmethod
    def _render_chart_json(parsed) -> bytes:  # noqa: ANN001
        # Compact time-series shape for chart libs (ECharts / Highcharts /
        # Chart.js all accept ``[[timestamp_ms, value], ...]`` natively).
        # ``timestamp`` from the measurement is already unix seconds in UTC —
        # multiplied by 1000 for milliseconds. ``value`` is left as ``null``
        # for missing samples so gaps in one channel don't align others.
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
                    [row.timestamp * 1000, getattr(row, key)]
                    for row in parsed.rows
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

    @staticmethod
    def _render_notes(parsed) -> bytes:  # noqa: ANN001
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

    @staticmethod
    def _render_passport(passport: MeasurementPassportDto) -> bytes:
        # ``values`` may contain datetimes and other non-JSON types injected by
        # the details parser — ``default=str`` renders them safely.
        return json.dumps(
            asdict(passport),
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")

    async def _upload_and_register(
        self,
        *,
        payload: bytes,
        s3_key: str,
    ) -> File | None:
        try:
            await self._storage.upload_file(BytesIO(payload), s3_key)
        except Exception:
            logger.exception("Failed to upload SPO file to S3 (key=%s).", s3_key)
            return None
        return await self._file_repo.create(CreateFileDTO(file=s3_key))
