"""Fetches dynamogram files from ABAI GDIS and persists them as Dynamogram rows.

Picks the dynamogram closest *before* repair.start_time and closest *after*
repair.end_time. Each file is downloaded, uploaded to S3, registered in the
``File`` table, then linked via a ``Dynamogram`` row.

Idempotent: if a Dynamogram with the same (well_id, snapshot_time) already
exists, no re-download happens.
"""

from datetime import datetime
from io import BytesIO

from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.models.file import File
from apps.files.repositories.file import FileRepository
from apps.repairs.models.repair import Repair
from apps.wells.dto.internal.repositories.dynamogram import CreateDynamogramDTO
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.repositories.dynamogram import DynamogramRepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage
from shared.integrations.abai.api.client import AbaiAsyncClient, AbaiFile

logger = get_logger(__name__)


class AbaiDynamogramFetcher:
    def __init__(
        self,
        abai_client: AbaiAsyncClient,
        storage: AiobotoFileStorage,
        file_repo: FileRepository,
        dynamogram_repo: DynamogramRepository,
    ) -> None:
        self._abai = abai_client
        self._storage = storage
        self._file_repo = file_repo
        self._dynamogram_repo = dynamogram_repo

    async def fetch_before_after(
        self,
        repair: Repair,
        abai_well_id: int,
        *,
        well_id: int | None = None,
    ) -> tuple[Dynamogram | None, Dynamogram | None]:
        effective_well_id = well_id if well_id is not None else repair.well_id

        files = await self._list_dynamogram_files(abai_well_id)
        logger.info(
            "ABAI GDIS returned %s dynamogram files for abai_well_id=%s "
            "(repair id=%s).",
            len(files),
            abai_well_id,
            repair.id,
        )
        if not files:
            return None, None

        before_file = self._closest_before(files, repair.start_time)
        after_file = (
            self._closest_after(files, repair.end_time)
            if repair.end_time is not None
            else None
        )
        logger.info(
            "Dynamogram picks for repair id=%s: before=%s after=%s (start=%s end=%s).",
            repair.id,
            getattr(before_file, "file_name", None),
            getattr(after_file, "file_name", None),
            repair.start_time,
            repair.end_time,
        )

        if effective_well_id is None:
            logger.warning(
                "Repair id=%s has no well_id (repair.well_id and abai→well "
                "lookup both empty) → cannot persist dynamograms.",
                repair.id,
            )
            return None, None

        before = (
            await self._persist(effective_well_id, before_file) if before_file else None
        )
        after = (
            await self._persist(effective_well_id, after_file) if after_file else None
        )
        return before, after

    async def fetch_all_for_well(
        self,
        *,
        well_id: int,
        abai_well_id: int,
    ) -> tuple[int, int, int]:
        """Скачать и сохранить все динамограммы скважины из ABAI GDIS.

        Возвращает (created, skipped, failed). Идемпотентно: уже сохранённые
        (well_id, snapshot_time) не перекачиваются. measure_date в ABAI — дата
        без времени, поэтому из нескольких файлов за один день сохранится
        только первый.
        """
        files = await self._list_dynamogram_files(abai_well_id)
        created = skipped = failed = 0
        for abai_file in files:
            snapshot_time = self._parse_measure_date(abai_file)
            if snapshot_time is None:
                failed += 1
                continue
            existing = await self._dynamogram_repo.get_by_well_id_and_snapshot_time(
                well_id=well_id,
                snapshot_time=snapshot_time,
            )
            if existing is not None:
                skipped += 1
                continue
            row = await self._persist(well_id, abai_file)
            if row is None:
                failed += 1
            else:
                created += 1
        return created, skipped, failed

    async def _list_dynamogram_files(self, abai_well_id: int) -> list[AbaiFile]:
        try:
            result = await self._abai.get_gdis_results(abai_well_id)
        except Exception:
            logger.exception(
                "ABAI GDIS request failed for well abai_id=%s.",
                abai_well_id,
            )
            return []
        return list(result.dynamogram_files)

    @staticmethod
    def _parse_measure_date(file: AbaiFile) -> datetime | None:
        if not file.measure_date:
            return None
        try:
            return datetime.strptime(file.measure_date, "%d.%m.%Y")  # noqa: DTZ007
        except ValueError:
            return None

    @staticmethod
    def _as_naive(value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value.tzinfo is not None else value

    @classmethod
    def _closest_before(
        cls,
        files: list[AbaiFile],
        at: datetime,
    ) -> AbaiFile | None:
        at = cls._as_naive(at)
        candidates: list[tuple[datetime, AbaiFile]] = []
        for f in files:
            d = cls._parse_measure_date(f)
            if d is not None and d <= at:
                candidates.append((d, f))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @classmethod
    def _closest_after(
        cls,
        files: list[AbaiFile],
        at: datetime,
    ) -> AbaiFile | None:
        at = cls._as_naive(at)
        candidates: list[tuple[datetime, AbaiFile]] = []
        for f in files:
            d = cls._parse_measure_date(f)
            if d is not None and d >= at:
                candidates.append((d, f))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    async def _persist(
        self,
        well_id: int,
        abai_file: AbaiFile,
    ) -> Dynamogram | None:
        snapshot_time = self._parse_measure_date(abai_file)
        if snapshot_time is None:
            return None
        if snapshot_time.tzinfo is not None:
            snapshot_time = snapshot_time.replace(tzinfo=None)

        existing = await self._dynamogram_repo.get_by_well_id_and_snapshot_time(
            well_id=well_id,
            snapshot_time=snapshot_time,
        )
        if existing is not None:
            return existing

        file_row = await self._download_and_register(abai_file)
        if file_row is None:
            return None

        return await self._dynamogram_repo.create(
            CreateDynamogramDTO(
                file_id=file_row.id,
                snapshot_time=snapshot_time,
                well_id=well_id,
            ),
        )

    async def _download_and_register(self, abai_file: AbaiFile) -> File | None:
        try:
            payload = await self._download_bytes(abai_file)
        except Exception:
            logger.exception(
                "Failed to download dynamogram attachment abai_file_id=%s.",
                abai_file.id,
            )
            return None

        s3_key = f"dynamograms/{abai_file.id}/{abai_file.file_name}"
        try:
            await self._storage.upload_file(BytesIO(payload), s3_key)
        except Exception:
            logger.exception("Failed to upload dynamogram to S3 (key=%s).", s3_key)
            return None

        return await self._file_repo.create(CreateFileDTO(file=s3_key))

    async def _download_bytes(self, abai_file: AbaiFile) -> bytes:
        await self._abai._ensure_login()  # noqa: SLF001
        headers = {"Accept": "*/*"}
        token = self._abai._xsrf_token  # noqa: SLF001
        if token:
            headers["X-XSRF-TOKEN"] = token
        async with self._abai._client.stream(  # noqa: SLF001
            "GET",
            f"/ru/attachments/{abai_file.id}",
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = [chunk async for chunk in resp.aiter_bytes() if chunk]
            return b"".join(chunks)
