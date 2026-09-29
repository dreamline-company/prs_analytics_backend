"""Runs AI processors and persists their outputs.

Encapsulates the read-existing → skip-if-current → run-processor → upsert
loop so ``FillRepairAnalytics`` stays about orchestration, not persistence.
"""

import mimetypes
from dataclasses import dataclass, field

from apps.files.repositories.file import FileRepository
from apps.org.repositories import UniqueBrigadeRepository
from apps.repairs.dto.internal.repositories.ai_results import (
    CreateRepairAIAnalysisDTO,
    CreateRepairDynamogramAIResultDTO,
    CreateRepairSPOAIResultDTO,
    UpdateRepairAIAnalysisDTO,
    UpdateRepairDynamogramAIResultDTO,
    UpdateRepairSPOAIResultDTO,
)
from apps.repairs.models.analytics import (
    AI_STATUS_COMPLETED,
    RepairAIAnalysis,
    RepairDynamogramAIResult,
    RepairSPOAIResult,
)
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.services.error_screens import list_repair_error_screens
from apps.repairs.tasks.fill_analytics.inputs import overall_fingerprint
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.models.spo import SPO
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage, FileNotExistError
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

from .base import TokenUsage
from .dynamogram_processor import DynamogramAIProcessor, DynamogramProcessingInput
from .overall_processor import (
    OverallAIProcessor,
    OverallProcessingInput,
    OverallViolationDTO,
)
from .spo_processor import SPOAIProcessor, SPOProcessingInput

logger = get_logger(__name__)


@dataclass(slots=True)
class AICoordinator:
    """Runs the three processors and stores results."""

    dynamogram_processor: DynamogramAIProcessor
    spo_processor: SPOAIProcessor
    overall_processor: OverallAIProcessor
    dynamogram_ai_repo: RepairDynamogramAIResultRepository
    spo_ai_repo: RepairSPOAIResultRepository
    overall_ai_repo: RepairAIAnalysisRepository
    file_repo: FileRepository
    storage: AiobotoFileStorage
    repair_brigade_repo: RepairBrigadeRepository
    unique_brigade_repo: UniqueBrigadeRepository
    cm_brigade_repo: CMBrigadeRepository
    cm_brigade_error_screen_repo: CMBrigadeErrorScreenRepository
    _usage_by_repair: dict[int, TokenUsage] = field(default_factory=dict)

    async def process_dynamogram(
        self,
        *,
        dynamogram: Dynamogram,
        role: str,
        repair_id: int,
    ) -> RepairDynamogramAIResult:
        existing = await self.dynamogram_ai_repo.get_by_dynamogram_id(dynamogram.id)
        if self._is_current(existing, self.dynamogram_processor.prompt_version):
            return existing

        s3_key = await self._resolve_s3_key(dynamogram.file_id)
        image_bytes = await self._download_bytes(s3_key)
        image_mime = self._guess_mime(s3_key, default="image/png")
        result = await self.dynamogram_processor.process(
            DynamogramProcessingInput(
                dynamogram=dynamogram,
                role=role,
                s3_key=s3_key or "",
                repair_id=repair_id,
                image_bytes=image_bytes,
                image_mime=image_mime,
            ),
        )
        self._record_usage(repair_id, result.usage)

        if existing is None:
            return await self.dynamogram_ai_repo.create(
                CreateRepairDynamogramAIResultDTO(
                    dynamogram_id=dynamogram.id,
                    status=result.status,
                    model_name=result.model_name,
                    prompt_version=result.prompt_version,
                    result=result.result,
                    error=result.error,
                    processed_at=result.processed_at,
                ),
            )
        return await self.dynamogram_ai_repo.update_by_dynamogram_id(
            dynamogram_id=dynamogram.id,
            data=UpdateRepairDynamogramAIResultDTO(
                status=result.status,
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                result=result.result,
                error=result.error,
                processed_at=result.processed_at,
            ),
        )

    async def process_spo(
        self,
        *,
        spo: SPO,
        repair_id: int,
    ) -> RepairSPOAIResult:
        existing = await self.spo_ai_repo.get_by_spo_id(spo.id)
        if self._is_current(existing, self.spo_processor.prompt_version):
            return existing

        raw_key = await self._resolve_s3_key(spo.file_id) or ""
        chart_key = await self._resolve_s3_key(spo.chart_file_id)
        notes_key = await self._resolve_s3_key(spo.notes_file_id)

        chart_text = await self._download_text(chart_key)
        notes_text = await self._download_text(notes_key)

        result = await self.spo_processor.process(
            SPOProcessingInput(
                spo=spo,
                raw_s3_key=raw_key,
                chart_s3_key=chart_key,
                notes_s3_key=notes_key,
                repair_id=repair_id,
                chart_text=chart_text,
                notes_text=notes_text,
            ),
        )
        self._record_usage(repair_id, result.usage)

        if existing is None:
            return await self.spo_ai_repo.create(
                CreateRepairSPOAIResultDTO(
                    spo_id=spo.id,
                    status=result.status,
                    model_name=result.model_name,
                    prompt_version=result.prompt_version,
                    result=result.result,
                    error=result.error,
                    processed_at=result.processed_at,
                ),
            )
        return await self.spo_ai_repo.update_by_spo_id(
            spo_id=spo.id,
            data=UpdateRepairSPOAIResultDTO(
                status=result.status,
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                result=result.result,
                error=result.error,
                processed_at=result.processed_at,
            ),
        )

    async def process_overall(  # noqa: PLR0913
        self,
        *,
        analytics_id: int,
        repair: Repair,
        dynamogram_before: RepairDynamogramAIResult | None,
        dynamogram_after: RepairDynamogramAIResult | None,
        spo_results: list[RepairSPOAIResult],
        inputs_fingerprint: str | None = None,
    ) -> RepairAIAnalysis:
        existing = await self.overall_ai_repo.get_by_analytics_id(analytics_id)
        # Нарушения нужны и для отпечатка: они входят в промпт, и новое
        # нарушение после первого вердикта должно его пересчитать.
        violations = await self._fetch_repair_violations(repair)
        fingerprint = overall_fingerprint(
            inputs_fingerprint or "",
            end_time=repair.end_time,
            violations=[(v.timestamp, v.description) for v in violations],
            spo_ai_outcomes={
                r.spo_id: (r.status, r.prompt_version) for r in spo_results
            },
        )
        # Вердикт актуален, только если и промпт, и набор входов те же: данные
        # приходят асинхронно, и вердикт по неполным входам должен обновиться.
        if (
            self._is_current(existing, self.overall_processor.prompt_version)
            and existing.inputs_fingerprint == fingerprint
        ):
            return existing

        result = await self.overall_processor.process(
            OverallProcessingInput(
                repair=repair,
                dynamogram_before_result=(
                    dynamogram_before.result if dynamogram_before else None
                ),
                dynamogram_after_result=(
                    dynamogram_after.result if dynamogram_after else None
                ),
                spo_results=[r.result for r in spo_results if r.result is not None],
                violations=violations,
            ),
        )
        self._record_usage(repair.id, result.usage)

        if existing is None:
            return await self.overall_ai_repo.create(
                CreateRepairAIAnalysisDTO(
                    analytics_id=analytics_id,
                    status=result.status,
                    model_name=result.model_name,
                    prompt_version=result.prompt_version,
                    result=result.result,
                    error=result.error,
                    processed_at=result.processed_at,
                    inputs_fingerprint=fingerprint,
                ),
            )
        return await self.overall_ai_repo.update_by_analytics_id(
            analytics_id=analytics_id,
            data=UpdateRepairAIAnalysisDTO(
                status=result.status,
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                result=result.result,
                error=result.error,
                processed_at=result.processed_at,
                inputs_fingerprint=fingerprint,
            ),
        )

    @staticmethod
    def _is_current(row, prompt_version: str) -> bool:  # noqa: ANN001
        return (
            row is not None
            and row.status == AI_STATUS_COMPLETED
            and row.prompt_version == prompt_version
        )

    async def _resolve_s3_key(self, file_id: int | None) -> str | None:
        if file_id is None:
            return None
        file_row = await self.file_repo.get_by_id(file_id)
        return file_row.file if file_row is not None else None

    async def _download_bytes(self, s3_key: str | None) -> bytes | None:
        if not s3_key:
            return None
        try:
            buf = await self.storage.download_file(s3_key)
        except FileNotExistError:
            logger.warning("S3 object missing for AI input: %s", s3_key)
            return None
        return buf.getvalue()

    async def _download_text(
        self,
        s3_key: str | None,
        *,
        encoding: str = "utf-8",
    ) -> str | None:
        payload = await self._download_bytes(s3_key)
        if payload is None:
            return None
        return payload.decode(encoding, errors="replace")

    @staticmethod
    def _guess_mime(s3_key: str | None, *, default: str) -> str:
        if not s3_key:
            return default
        guessed, _ = mimetypes.guess_type(s3_key)
        return guessed or default

    def _record_usage(self, repair_id: int, usage: TokenUsage) -> None:
        bucket = self._usage_by_repair.setdefault(repair_id, TokenUsage())
        bucket.add(usage)

    def pop_repair_usage(self, repair_id: int) -> TokenUsage:
        return self._usage_by_repair.pop(repair_id, TokenUsage())

    async def _fetch_repair_violations(
        self,
        repair: Repair,
    ) -> list[OverallViolationDTO]:
        screens = await list_repair_error_screens(
            repair,
            repair_brigade_repo=self.repair_brigade_repo,
            unique_brigade_repo=self.unique_brigade_repo,
            cm_brigade_repo=self.cm_brigade_repo,
            cm_brigade_error_screen_repo=self.cm_brigade_error_screen_repo,
        )
        return [
            OverallViolationDTO(
                timestamp=s.timestamp.isoformat(),
                description=s.description or "",
            )
            for s in sorted(screens, key=lambda s: s.timestamp)
        ]
