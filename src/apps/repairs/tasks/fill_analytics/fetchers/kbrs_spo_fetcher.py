"""Fetches SPO (спуско-подъёмные операции) data from kbrs / Toucan.

Brigade-centric: SPO measurements in kbrs are owned by the brigade's device,
not by the well. The brigade number for a repair lives on RepairSummary
(populated from the parsed XLSX summaries), so this fetcher:

  1. Collects distinct ``brigade_number`` values from ``RepairSummary`` rows
     of the repair.
  2. Resolves each brigade to a kbrs ``(owner_id, device_id)`` via an injected
     ``brigade_resolver``. Returning ``None`` from the resolver skips that
     brigade.
  3. For each resolved brigade, pulls the latest measurement in the repair
     window from kbrs and persists one ``SPO`` row.

Each kbrs measurement we pull becomes one SPO row, with:
  * ``file_id``       — raw binary measurement payload (the master file).
  * ``chart_file_id`` — CSV export of the measurement (тот самый «график»).
  * ``notes_file_id`` — JSON metadata blob (channels, time range, record count).

The kbrs RPC client is synchronous, so I/O is offloaded to a thread.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from io import BytesIO

from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.models.file import File
from apps.files.repositories.file import FileRepository
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.wells.dto.internal.repositories.spo import CreateSPODTO, UpdateSPODTO
from apps.wells.models.spo import SPO
from apps.wells.repositories.spo import SPORepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage
from shared.integrations.kbrs.api.client import ToucanBackendClient
from shared.integrations.kbrs.api.dtos import (
    DeviceSearchFilterDto,
    LoadMeasurementRequestDto,
    MeasureListFilterDto,
)
from shared.integrations.kbrs.api.enums import MeasureDateCondition

logger = get_logger(__name__)


BrigadeResolver = Callable[[int], tuple[int, int] | None]
"""``brigade_number -> (owner_id, device_id) | None``."""


def make_kbrs_brigade_resolver(client: ToucanBackendClient) -> BrigadeResolver:
    """Default resolver: search kbrs devices by brigade number string.

    Matches when the brigade number appears in the device description or
    device_id. Returns the first match. If kbrs encodes brigades differently
    in your environment, replace this with a custom resolver (e.g. a static
    config-driven map).
    """

    def resolver(brigade_number: int) -> tuple[int, int] | None:
        devices = client.search_devices(
            DeviceSearchFilterDto(query=str(brigade_number)),
        )
        if not devices:
            return None
        device = devices[0]
        try:
            return device.owner_id, int(device.device_id)
        except (TypeError, ValueError):
            return None

    return resolver


class KbrsSPOFetcher:
    def __init__(  # noqa: PLR0913
        self,
        toucan_client: ToucanBackendClient,
        storage: AiobotoFileStorage,
        file_repo: FileRepository,
        spo_repo: SPORepository,
        summary_repo: RepairSummaryRepository,
        brigade_resolver: BrigadeResolver,
    ) -> None:
        self._toucan = toucan_client
        self._storage = storage
        self._file_repo = file_repo
        self._spo_repo = spo_repo
        self._summary_repo = summary_repo
        self._brigade_resolver = brigade_resolver

    async def fetch_for_repair(self, repair: Repair) -> list[SPO]:
        if repair.well_id is None:
            logger.warning(
                "SPO skipped for repair id=%s — repair.well_id is None.",
                repair.id,
            )
            return []

        brigade_numbers = await self._brigade_numbers_for_repair(repair.id)
        logger.info(
            "SPO for repair id=%s: brigade_numbers=%s",
            repair.id,
            brigade_numbers,
        )
        if not brigade_numbers:
            logger.warning(
                "SPO skipped for repair id=%s — no brigade_number in "
                "RepairSummary (fill summaries first).",
                repair.id,
            )
            return []

        spos: list[SPO] = []
        for brigade_number in brigade_numbers:
            resolved = self._brigade_resolver(brigade_number)
            if resolved is None:
                logger.warning(
                    "Brigade %s (repair id=%s) has no kbrs device mapping; "
                    "skipped.",
                    brigade_number,
                    repair.id,
                )
                continue
            owner_id, device_id = resolved
            logger.info(
                "Brigade %s → kbrs owner_id=%s device_id=%s (repair id=%s).",
                brigade_number,
                owner_id,
                device_id,
                repair.id,
            )
            spo = await self._fetch_one(
                repair=repair,
                owner_id=owner_id,
                device_id=device_id,
            )
            if spo is None:
                logger.warning(
                    "kbrs returned no SPO for brigade %s (repair id=%s, "
                    "owner_id=%s, device_id=%s) — no measurement in window "
                    "or upload failed.",
                    brigade_number,
                    repair.id,
                    owner_id,
                    device_id,
                )
            else:
                spos.append(spo)
        return spos

    async def _brigade_numbers_for_repair(self, repair_id: int) -> list[int]:
        summaries = await self._summary_repo.list_by_repair_id(repair_id)
        seen: set[int] = set()
        ordered: list[int] = []
        for summary in summaries:
            if summary.brigade_number not in seen:
                seen.add(summary.brigade_number)
                ordered.append(summary.brigade_number)
        return ordered

    async def _fetch_one(
        self,
        *,
        repair: Repair,
        owner_id: int,
        device_id: int,
    ) -> SPO | None:
        end = repair.end_time or datetime.now()  # noqa: DTZ005
        measure = await asyncio.to_thread(
            self._pick_measurement,
            owner_id=owner_id,
            device_id=device_id,
            start=repair.start_time,
            end=end,
        )
        if measure is None:
            return None

        snapshot_time = measure.parsed.start or repair.start_time

        existing = await self._spo_repo.get_by_well_id_and_snapshot_time(
            well_id=repair.well_id,
            snapshot_time=snapshot_time,
        )
        if existing is not None and existing.chart_file_id and existing.notes_file_id:
            return existing

        load_raw = self._toucan.measurement_service.load_raw_measurement
        raw_bytes = await asyncio.to_thread(load_raw, measure.request)
        csv_bytes = self._render_csv(measure.parsed)
        notes_bytes = self._render_notes(measure.parsed)

        prefix = f"spo/{repair.well_id}/{measure.request.measure_id}"
        master_file = await self._upload_and_register(
            payload=raw_bytes,
            s3_key=f"{prefix}/raw.bin",
        )
        chart_file = await self._upload_and_register(
            payload=csv_bytes,
            s3_key=f"{prefix}/chart.csv",
        )
        notes_file = await self._upload_and_register(
            payload=notes_bytes,
            s3_key=f"{prefix}/notes.json",
        )

        if master_file is None:
            return None

        if existing is None:
            return await self._spo_repo.create(
                CreateSPODTO(
                    file_id=master_file.id,
                    chart_file_id=chart_file.id if chart_file else None,
                    notes_file_id=notes_file.id if notes_file else None,
                    snapshot_time=snapshot_time,
                    well_id=repair.well_id,
                ),
            )
        return await self._spo_repo.update(
            data=UpdateSPODTO(
                file_id=master_file.id,
                chart_file_id=chart_file.id if chart_file else None,
                notes_file_id=notes_file.id if notes_file else None,
            ),
            filters=(SPO.id == existing.id,),
        )

    def _pick_measurement(
        self,
        *,
        owner_id: int,
        device_id: int,
        start: datetime,
        end: datetime,
    ) -> "_PickedMeasurement | None":
        measures: Sequence = self._toucan.list_measures(
            MeasureListFilterDto(
                owner_id=owner_id,
                device_id=str(device_id),
                condition_date=MeasureDateCondition.INTERVAL,
                date_from=start,
                date_to=end + timedelta(days=1),
                page_count=50,
            ),
        )
        if not measures:
            return None
        selected = measures[0]
        request = LoadMeasurementRequestDto(
            measure_id=selected.measure_id,
            device_id=selected.device_id,
            update_offset=selected.offset,
        )
        parsed = self._toucan.load_measurement(request)
        return _PickedMeasurement(request=request, parsed=parsed)

    @staticmethod
    def _render_csv(parsed) -> bytes:  # noqa: ANN001
        header = "timestamp,datetime,hook_weight_t,h2s_mg_m3,ch4_percent\n"
        lines = [header, *(
            f"{row.timestamp},"
            f"{row.datetime.isoformat() if row.datetime else ''},"
            f"{row.hook_weight_t if row.hook_weight_t is not None else ''},"
            f"{row.h2s_mg_m3 if row.h2s_mg_m3 is not None else ''},"
            f"{row.ch4_percent if row.ch4_percent is not None else ''}\n"
            for row in parsed.rows
        )]
        return "".join(lines).encode("utf-8")

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


class _PickedMeasurement:
    __slots__ = ("parsed", "request")

    def __init__(
        self,
        request: LoadMeasurementRequestDto,
        parsed,  # noqa: ANN001
    ) -> None:
        self.request = request
        self.parsed = parsed
