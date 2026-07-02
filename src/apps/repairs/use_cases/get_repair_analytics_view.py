"""Builds the frontend view of one repair's analytics.

Aggregates data from the app DB (analytics, dynamograms, SPOs, AI results)
and the CM DB (brigade error screens referenced by ``cm_screen_id``).
"""

from starlette import status

from apps.repairs.dto.internal.analytics_view import (
    AIResultDTO,
    BrigadeErrorScreenDTO,
    DynamogramsPairDTO,
    DynamogramWithAIResultDTO,
    RepairAnalyticsViewDTO,
    SPOWithAIResultDTO,
)
from apps.repairs.dto.queries.analytics_view import GetRepairAnalyticsViewQuery
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.analytics import (
    RepairAnalyticsBrigadeErrorScreenRepository,
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
)
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from shared.errors import HttpError
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)


class RepairAnalyticsNotFoundError(HttpError):
    message = "Repair analytics not found."
    code = "repair_analytics_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetRepairAnalyticsViewUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        repair_repository: RepairRepository,
        analytics_repository: RepairAnalyticsRepository,
        analytics_dynamogram_repository: RepairAnalyticsDynamogramRepository,
        analytics_brigade_error_screen_repository: (
            RepairAnalyticsBrigadeErrorScreenRepository
        ),
        dynamogram_repository: DynamogramRepository,
        spo_repository: SPORepository,
        dynamogram_ai_repository: RepairDynamogramAIResultRepository,
        spo_ai_repository: RepairSPOAIResultRepository,
        overall_ai_repository: RepairAIAnalysisRepository,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
    ) -> None:
        self.repair_repository = repair_repository
        self.analytics_repository = analytics_repository
        self.analytics_dynamogram_repository = analytics_dynamogram_repository
        self.analytics_brigade_error_screen_repository = (
            analytics_brigade_error_screen_repository
        )
        self.dynamogram_repository = dynamogram_repository
        self.spo_repository = spo_repository
        self.dynamogram_ai_repository = dynamogram_ai_repository
        self.spo_ai_repository = spo_ai_repository
        self.overall_ai_repository = overall_ai_repository
        self.cm_brigade_error_screen_repository = cm_brigade_error_screen_repository

    async def execute(
        self,
        query: GetRepairAnalyticsViewQuery,
    ) -> RepairAnalyticsViewDTO:
        repair = await self.repair_repository.get_by_id(query.repair_id)
        if repair is None:
            raise RepairAnalyticsNotFoundError(
                details={"repair_id": query.repair_id},
            )
        analytics = await self.analytics_repository.get_by_repair_id(query.repair_id)
        if analytics is None:
            raise RepairAnalyticsNotFoundError(
                details={"repair_id": query.repair_id},
            )

        dynamograms = await self._build_dynamograms(analytics.id)
        spos = await self._build_spos(
            well_id=repair.well_id,
            start_time=repair.start_time,
            end_time=repair.end_time,
        )
        error_screens = await self._build_error_screens(analytics.id)
        overall = await self.overall_ai_repository.get_by_analytics_id(analytics.id)

        return RepairAnalyticsViewDTO(
            analytics_id=analytics.id,
            repair_id=analytics.repair_id,
            is_finalized=analytics.is_finalized,
            repair_docs_id=analytics.repair_docs_id,
            summary_id=analytics.summary_id,
            dynamograms=dynamograms,
            spos=spos,
            error_screens=error_screens,
            overall_ai_analysis=(
                AIResultDTO.model_validate(overall) if overall is not None else None
            ),
        )

    async def _build_dynamograms(self, analytics_id: int) -> DynamogramsPairDTO:
        link = await self.analytics_dynamogram_repository.get_by_analytics_id(
            analytics_id,
        )
        if link is None:
            return DynamogramsPairDTO()

        ids = [
            i
            for i in (link.dynamogram_before_id, link.dynamogram_after_id)
            if i is not None
        ]
        dynamograms = await self.dynamogram_repository.list_by_ids(ids)
        by_id = {d.id: d for d in dynamograms}

        ai_results = await self.dynamogram_ai_repository.list_by_dynamogram_ids(ids)
        ai_by_dynamogram_id = {r.dynamogram_id: r for r in ai_results}

        def build(dynamogram_id: int | None) -> DynamogramWithAIResultDTO | None:
            if dynamogram_id is None or dynamogram_id not in by_id:
                return None
            dto = DynamogramWithAIResultDTO.model_validate(by_id[dynamogram_id])
            ai = ai_by_dynamogram_id.get(dynamogram_id)
            if ai is not None:
                dto.ai_result = AIResultDTO.model_validate(ai)
            return dto

        return DynamogramsPairDTO(
            before=build(link.dynamogram_before_id),
            after=build(link.dynamogram_after_id),
        )

    async def _build_spos(
        self,
        *,
        well_id: int | None,
        start_time,  # noqa: ANN001
        end_time,  # noqa: ANN001
    ) -> list[SPOWithAIResultDTO]:
        if well_id is None:
            return []
        spos = await self.spo_repository.list_by_well_id_in_window(
            well_id=well_id,
            start=start_time,
            end=end_time,
        )
        ai_results = await self.spo_ai_repository.list_by_spo_ids([s.id for s in spos])
        ai_by_spo_id = {r.spo_id: r for r in ai_results}

        result: list[SPOWithAIResultDTO] = []
        for spo in spos:
            dto = SPOWithAIResultDTO.model_validate(spo)
            ai = ai_by_spo_id.get(spo.id)
            if ai is not None:
                dto.ai_result = AIResultDTO.model_validate(ai)
            result.append(dto)
        return result

    async def _build_error_screens(
        self,
        analytics_id: int,
    ) -> list[BrigadeErrorScreenDTO]:
        links = (
            await self.analytics_brigade_error_screen_repository.list_by_analytics_id(
                analytics_id,
            )
        )
        cm_screen_ids = [link.cm_screen_id for link in links]
        screens = await self.cm_brigade_error_screen_repository.list_by_ids(
            cm_screen_ids,
        )
        return [BrigadeErrorScreenDTO.model_validate(s) for s in screens]
