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
     on re-fetch) via ``SpoMeasurementPersister``. No second RPC.

This is the fallback path: repairs whose measurements the KBRS poller has not
seen (history before the poller, poller downtime) — the primary path links
``kbrs_measure`` rows without any Toucan RPC (``repairs.link_spo``).

Parallelism is bounded by ``ToucanClientPool`` size — each in-flight RPC
holds a client checked out from the pool.

Each stored SPO row carries:
  * ``file_id``       — raw binary measurement payload (master file).
  * ``chart_file_id`` — CSV export of the chart.
  * ``notes_file_id`` — JSON metadata blob (channels, time range, event count).
"""

import asyncio
import re
from collections.abc import Sequence
from datetime import datetime, timedelta

from apps.files.repositories.file import FileRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.repairs.models.repair import Repair
from apps.repairs.tasks.fill_analytics.fetchers.spo_persist import (
    SpoMeasurementPersister,
)
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


def owner_id_by_ngdu_abai_id(ngdu_abai_id: int) -> int | None:
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
        self._get_ngdu_for_well = get_ngdu_for_well
        self._persister = SpoMeasurementPersister(
            storage=storage,
            file_repo=file_repo,
            spo_repo=spo_repo,
            spo_event_repo=spo_event_repo,
        )

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

        owner_id = owner_id_by_ngdu_abai_id(ngdu.abai_id)
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

        return await self._persister.persist(
            well_id=well_id,
            measure_id=measure.measure_id,
            raw_bytes=raw,
            full=full,
            fallback_snapshot_time=repair.start_time,
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
