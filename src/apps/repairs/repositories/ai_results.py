from apps.repairs.dto.internal.repositories.ai_results import (
    CreateRepairAIAnalysisDTO,
    CreateRepairDynamogramAIResultDTO,
    CreateRepairSPOAIResultDTO,
    UpdateRepairAIAnalysisDTO,
    UpdateRepairDynamogramAIResultDTO,
    UpdateRepairSPOAIResultDTO,
)
from apps.repairs.models.analytics import (
    RepairAIAnalysis,
    RepairDynamogramAIResult,
    RepairSPOAIResult,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairDynamogramAIResultRepository(
    AsyncAlchemyRepository[
        CreateRepairDynamogramAIResultDTO,
        UpdateRepairDynamogramAIResultDTO,
        RepairDynamogramAIResult,
    ],
):
    model = RepairDynamogramAIResult

    async def get_by_dynamogram_id(
        self,
        dynamogram_id: int,
    ) -> RepairDynamogramAIResult | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairDynamogramAIResult.dynamogram_id == dynamogram_id,),
            ),
        )

    async def update_by_dynamogram_id(
        self,
        dynamogram_id: int,
        data: UpdateRepairDynamogramAIResultDTO,
    ) -> RepairDynamogramAIResult:
        return await self.update(
            data=data,
            filters=(RepairDynamogramAIResult.dynamogram_id == dynamogram_id,),
        )


class RepairSPOAIResultRepository(
    AsyncAlchemyRepository[
        CreateRepairSPOAIResultDTO,
        UpdateRepairSPOAIResultDTO,
        RepairSPOAIResult,
    ],
):
    model = RepairSPOAIResult

    async def get_by_spo_id(self, spo_id: int) -> RepairSPOAIResult | None:
        return await self.get_one(
            QuerySpec(filters=(RepairSPOAIResult.spo_id == spo_id,)),
        )

    async def update_by_spo_id(
        self,
        spo_id: int,
        data: UpdateRepairSPOAIResultDTO,
    ) -> RepairSPOAIResult:
        return await self.update(
            data=data,
            filters=(RepairSPOAIResult.spo_id == spo_id,),
        )


class RepairAIAnalysisRepository(
    AsyncAlchemyRepository[
        CreateRepairAIAnalysisDTO,
        UpdateRepairAIAnalysisDTO,
        RepairAIAnalysis,
    ],
):
    model = RepairAIAnalysis

    async def get_by_analytics_id(
        self,
        analytics_id: int,
    ) -> RepairAIAnalysis | None:
        return await self.get_one(
            QuerySpec(filters=(RepairAIAnalysis.analytics_id == analytics_id,)),
        )

    async def update_by_analytics_id(
        self,
        analytics_id: int,
        data: UpdateRepairAIAnalysisDTO,
    ) -> RepairAIAnalysis:
        return await self.update(
            data=data,
            filters=(RepairAIAnalysis.analytics_id == analytics_id,),
        )
