"""Fetches the ПОР/Акт PDF from ABAI for a repair.

ABAI returns one PDF per repair record that contains:
  * only ПОР while the repair is in progress;
  * ПОР + акт once the repair has finished.

Strategy:
  * always re-download the PDF (acts may be appended later);
  * compare its SHA-256 with the stored one — unchanged PDF is not re-uploaded
    and does not spawn a new ``File`` row (the fetcher now runs every quarter
    of an hour, without the check it would litter S3 and ``files_file``);
  * keep ``por_file_id`` always set;
  * set ``act_file_id`` only after the repair has finished.

This mirrors what the user described: «в начале только ПОР, в конце добавляется
акт в тот же PDF».
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.repositories.file import FileRepository
from apps.repairs.dto.internal.repositories.docs import (
    CreateRepairDocDTO,
    UpdateRepairDocDTO,
)
from apps.repairs.models.docs import RepairDoc
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.docs import RepairDocRepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage
from shared.integrations.abai.api.client import AbaiAsyncClient, AbaiFile

logger = get_logger(__name__)

DocAction = Literal["skip", "set_act", "upload"]


@dataclass(frozen=True, slots=True)
class DocFetchOutcome:
    doc: RepairDoc | None
    # Документ появился или изменился — повод пересчитать аналитику.
    changed: bool


def plan_doc_update(
    existing: RepairDoc | None,
    *,
    digest: str,
    is_finished: bool,
) -> DocAction:
    """Что делать со скачанным PDF относительно сохранённого документа.

    * ``upload`` — документа нет или содержимое изменилось;
    * ``set_act`` — PDF тот же, но ремонт завершился, а акт ещё не отмечен:
      достаточно указать актом уже загруженный файл;
    * ``skip`` — ничего не изменилось.
    """
    if existing is None or existing.por_file_id is None:
        return "upload"
    if existing.source_hash != digest:
        return "upload"
    if is_finished and existing.act_file_id is None:
        return "set_act"
    return "skip"


def content_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class AbaiRepairDocFetcher:
    def __init__(
        self,
        abai_client: AbaiAsyncClient,
        storage: AiobotoFileStorage,
        file_repo: FileRepository,
        repair_doc_repo: RepairDocRepository,
    ) -> None:
        self._abai = abai_client
        self._storage = storage
        self._file_repo = file_repo
        self._repair_doc_repo = repair_doc_repo

    async def fetch(
        self,
        repair: Repair,
        abai_well_id: int,
    ) -> DocFetchOutcome:
        existing = await self._repair_doc_repo.get_by_repair_id(repair.id)
        pdf = await self._find_pdf(repair, abai_well_id)
        if pdf is None:
            return DocFetchOutcome(doc=existing, changed=False)

        try:
            payload = await self._download_bytes(pdf)
        except Exception:
            logger.exception(
                "Failed to download PRS PDF abai_file_id=%s for repair_id=%s.",
                pdf.id,
                repair.id,
            )
            return DocFetchOutcome(doc=existing, changed=False)

        digest = content_digest(payload)
        is_finished = repair.end_time is not None
        action = plan_doc_update(existing, digest=digest, is_finished=is_finished)
        if action == "skip":
            return DocFetchOutcome(doc=existing, changed=False)
        if action == "set_act":
            doc = await self._repair_doc_repo.update_by_repair_id(
                repair_id=repair.id,
                data=UpdateRepairDocDTO(act_file_id=existing.por_file_id),
            )
            return DocFetchOutcome(doc=doc, changed=True)
        return await self._store(
            repair,
            pdf=pdf,
            payload=payload,
            digest=digest,
            existing=existing,
            is_finished=is_finished,
        )

    async def _store(  # noqa: PLR0913
        self,
        repair: Repair,
        *,
        pdf: AbaiFile,
        payload: bytes,
        digest: str,
        existing: RepairDoc | None,
        is_finished: bool,
    ) -> DocFetchOutcome:
        s3_key = f"repair_docs/{repair.id}/{pdf.id}_{pdf.file_name}"
        try:
            await self._storage.upload_file(BytesIO(payload), s3_key)
        except Exception:
            logger.exception("Failed to upload PRS PDF to S3 (key=%s).", s3_key)
            return DocFetchOutcome(doc=existing, changed=False)

        file_row = await self._file_repo.create(CreateFileDTO(file=s3_key))

        if existing is None:
            doc = await self._repair_doc_repo.create(
                CreateRepairDocDTO(
                    repair_id=repair.id,
                    por_file_id=file_row.id,
                    act_file_id=file_row.id if is_finished else None,
                    source_hash=digest,
                ),
            )
            return DocFetchOutcome(doc=doc, changed=True)

        update = UpdateRepairDocDTO(por_file_id=file_row.id, source_hash=digest)
        if is_finished:
            update.act_file_id = file_row.id
        doc = await self._repair_doc_repo.update_by_repair_id(
            repair_id=repair.id,
            data=update,
        )
        return DocFetchOutcome(doc=doc, changed=True)

    async def _find_pdf(
        self,
        repair: Repair,
        abai_well_id: int,
    ) -> AbaiFile | None:
        try:
            records = await self._abai.get_prs_results(abai_well_id)
        except Exception:
            logger.exception(
                "ABAI PRS request failed for well abai_id=%s.",
                abai_well_id,
            )
            return None
        for record in records:
            if record.id == repair.abai_id and record.files:
                return record.files[0]
        return None

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
