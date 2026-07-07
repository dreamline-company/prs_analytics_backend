"""Fetches SPO (спуско-подъёмные операции) data from kbrs / Toucan.

NGDU-centric routing:
  1. Resolve the well's NGDU via ``GetNGDUForWellUseCase`` (walks
     ``well_org → org.parent_id → …`` until an NGDU-typed org).
  2. Map the NGDU's ABAI id to a kbrs ``owner_id`` through
     ``shared.constants.kbrs.OWNERS_MAP``.
  3. ``list_devices(owner_id)`` — all devices of that NGDU.
  4. For each device, list measurements in the repair window
     (``[start_time, end_time+1d]``); for each measurement load its passport
     and compare ``passport.well`` (an integer well number) against the
     digits extracted from ``well.name`` (e.g. ``VMB_0177`` → ``177``).
  5. Every matching measurement is stored as one ``SPO`` row plus the
     accompanying event set (``repairs_spo_event``, replaced on re-fetch).

Each stored SPO row carries:
  * ``file_id``       — raw binary measurement payload (master file).
  * ``chart_file_id`` — CSV export of the chart.
  * ``notes_file_id`` — JSON metadata blob (channels, time range, event count).

The kbrs RPC client is synchronous, so I/O is offloaded to a thread.
"""

import asyncio
import json
import re
from collections.abc import Sequence
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
from shared.integrations.kbrs.api import ToucanDecodeError
from shared.integrations.kbrs.api.client import ToucanBackendClient
from shared.integrations.kbrs.api.dtos import (
    DeviceDto,
    LoadMeasurementRequestDto,
    MeasureListFilterDto,
    MeasurementFullDto,
    MeasureRowDto,
)
from shared.integrations.kbrs.api.enums import MeasureDateCondition

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
        toucan_client: ToucanBackendClient,
        storage: AiobotoFileStorage,
        file_repo: FileRepository,
        spo_repo: SPORepository,
        spo_event_repo: SPOEventRepository,
        get_ngdu_for_well: GetNGDUForWellUseCase,
    ) -> None:
        self._toucan = toucan_client
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

        devices: Sequence[DeviceDto] = await asyncio.to_thread(
            self._toucan.list_devices,
            owner_id,
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

        spos: list[SPO] = []
        for device in devices:
            spo = await self._fetch_for_device(
                repair=repair,
                well_id=well_id,
                target_well_number=target_well_number,
                owner_id=owner_id,
                device=device,
                start=repair.start_time,
                end=end,
            )
            if spo is not None:
                spos.append(spo)
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

        measures = await asyncio.to_thread(
            self._list_measures,
            owner_id=owner_id,
            device_id=device_id_int,
            start=start,
            end=end,
        )
        if not measures:
            return None

        for measure in measures:
            try:
                full = await asyncio.to_thread(
                    self._load_full,
                    measure_id=measure.measure_id,
                    device_id=measure.device_id,
                    update_offset=measure.offset,
                )
            except ToucanDecodeError as e:
                logger.warning("Measure is empty: %s", e)
                continue
            passport_well = full.details.passport.well
            if passport_well is None:
                continue
            if int(passport_well) != target_well_number:
                continue

            logger.info(
                "Repair id=%s: matched device_id=%s measure_id=%s "
                "(passport.well=%s == %s).",
                repair.id,
                device.device_id,
                measure.measure_id,
                passport_well,
                target_well_number,
            )

            spo = await self._persist_measurement(
                repair=repair,
                well_id=well_id,
                measure=measure,
                full=full,
            )
            if spo is not None:
                return spo
        return None

    def _list_measures(
        self,
        *,
        owner_id: int,
        device_id: int,
        start: datetime,
        end: datetime,
    ) -> list[MeasureRowDto]:
        return self._toucan.list_measures(
            MeasureListFilterDto(
                owner_id=owner_id,
                device_id=str(device_id),
                condition_date=MeasureDateCondition.INTERVAL,
                date_from=start,
                date_to=end + timedelta(days=1),
                page_count=50,
            ),
        )

    def _load_full(
        self,
        *,
        measure_id: int,
        device_id: int,
        update_offset: int,
    ) -> MeasurementFullDto:
        return self._toucan.load_full_measurement(
            LoadMeasurementRequestDto(
                measure_id=measure_id,
            ),
        )

    async def _persist_measurement(
        self,
        *,
        repair: Repair,
        well_id: int,
        measure: MeasureRowDto,
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
        if existing is not None and existing.chart_file_id and existing.notes_file_id:
            await self._persist_events(existing.id, events)
            return existing

        request = LoadMeasurementRequestDto(
            measure_id=measure.measure_id,
            device_id=measure.device_id,
            update_offset=measure.offset,
        )
        raw_bytes = await asyncio.to_thread(
            self._toucan.measurement_service.load_raw_measurement,
            request,
        )
        csv_bytes = self._render_csv(parsed)
        notes_bytes = self._render_notes(parsed)

        prefix = f"spo/{well_id}/{measure.measure_id}"
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
            spo = await self._spo_repo.create(
                CreateSPODTO(
                    file_id=master_file.id,
                    chart_file_id=chart_file.id if chart_file else None,
                    notes_file_id=notes_file.id if notes_file else None,
                    snapshot_time=snapshot_time,
                    well_id=well_id,
                ),
            )
        else:
            spo = await self._spo_repo.update(
                data=UpdateSPODTO(
                    file_id=master_file.id,
                    chart_file_id=chart_file.id if chart_file else None,
                    notes_file_id=notes_file.id if notes_file else None,
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
