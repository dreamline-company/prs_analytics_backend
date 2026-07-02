"""Runs AI processors and persists their outputs.

Encapsulates the read-existing → skip-if-current → run-processor → upsert
loop so ``FillRepairAnalytics`` stays about orchestration, not persistence.
"""

from dataclasses import dataclass

from apps.files.repositories.file import FileRepository
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
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.models.spo import SPO
from core import get_logger

from .dynamogram_processor import DynamogramAIProcessor, DynamogramProcessingInput
from .overall_processor import OverallAIProcessor, OverallProcessingInput
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
        result = await self.dynamogram_processor.process(
            DynamogramProcessingInput(
                dynamogram=dynamogram,
                role=role,
                s3_key=s3_key or "",
                repair_id=repair_id,
            ),
        )

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

        result = await self.spo_processor.process(
            SPOProcessingInput(
                spo=spo,
                raw_s3_key=raw_key,
                chart_s3_key=chart_key,
                notes_s3_key=notes_key,
                repair_id=repair_id,
            ),
        )

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

    async def process_overall(
        self,
        *,
        analytics_id: int,
        repair: Repair,
        dynamogram_before: RepairDynamogramAIResult | None,
        dynamogram_after: RepairDynamogramAIResult | None,
        spo_results: list[RepairSPOAIResult],
    ) -> RepairAIAnalysis:
        existing = await self.overall_ai_repo.get_by_analytics_id(analytics_id)
        if self._is_current(existing, self.overall_processor.prompt_version):
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
            ),
        )

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
