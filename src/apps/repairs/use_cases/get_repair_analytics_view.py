"""Builds the frontend view of one repair's analytics.

Aggregates data from the app DB (analytics, dynamograms, SPOs, AI results)
and the CM DB (brigade error screens referenced by ``cm_screen_id``).
"""

import json
from datetime import datetime

from starlette import status

from apps.files.repositories.file import FileRepository
from apps.repairs.dto.internal.analytics_view import (
    AIResultDTO,
    BrigadeErrorScreenDTO,
    DynamogramsPairDTO,
    DynamogramWithAIResultDTO,
    OverallAIResultDTO,
    OverallAIVerdictDTO,
    RepairAnalyticsViewDTO,
    RepairTransportViewDTO,
    SPOPassportDTO,
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
    RepairAnalyticsSPORepository,
)
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.repositories.transport import RepairTransportRepository
from apps.wells.repositories import WellRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage
from shared.errors import HttpError
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)

logger = get_logger(__name__)


class RepairAnalyticsNotFoundError(HttpError):
    message = "Repair analytics not found."
    code = "repair_analytics_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetRepairAnalyticsViewUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        repair_repository: RepairRepository,
        wells_repository: WellRepository,
        analytics_repository: RepairAnalyticsRepository,
        analytics_dynamogram_repository: RepairAnalyticsDynamogramRepository,
        analytics_brigade_error_screen_repository: (
            RepairAnalyticsBrigadeErrorScreenRepository
        ),
        analytics_spo_repository: RepairAnalyticsSPORepository,
        dynamogram_repository: DynamogramRepository,
        spo_repository: SPORepository,
        dynamogram_ai_repository: RepairDynamogramAIResultRepository,
        spo_ai_repository: RepairSPOAIResultRepository,
        overall_ai_repository: RepairAIAnalysisRepository,
        transport_repository: RepairTransportRepository,
        file_repository: FileRepository,
        storage: AiobotoFileStorage,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
        cm_media_url_header: str,
    ) -> None:
        self.repair_repository = repair_repository
        self.wells_repository = wells_repository
        self.analytics_repository = analytics_repository
        self.analytics_dynamogram_repository = analytics_dynamogram_repository
        self.analytics_brigade_error_screen_repository = (
            analytics_brigade_error_screen_repository
        )
        self.analytics_spo_repository = analytics_spo_repository
        self.dynamogram_repository = dynamogram_repository
        self.spo_repository = spo_repository
        self.dynamogram_ai_repository = dynamogram_ai_repository
        self.spo_ai_repository = spo_ai_repository
        self.overall_ai_repository = overall_ai_repository
        self.transport_repository = transport_repository
        self.file_repository = file_repository
        self.storage = storage
        self.cm_brigade_error_screen_repository = cm_brigade_error_screen_repository
        self.cm_media_url_header = cm_media_url_header

    async def execute(
        self,
        query: GetRepairAnalyticsViewQuery,
    ) -> RepairAnalyticsViewDTO:
        repair = await self.repair_repository.get_by_id(query.repair_id)
        well = await self.wells_repository.get_by_abai_id(abai_id=repair.abai_well_id)
        if repair is None:
            raise RepairAnalyticsNotFoundError(
                details={"repair_id": query.repair_id},
            )
        if well is None:
            raise RepairAnalyticsNotFoundError(
                details={"well_no_exists": repair.abai_well_id},
            )
        analytics = await self.analytics_repository.get_by_repair_id(query.repair_id)
        if analytics is None:
            raise RepairAnalyticsNotFoundError(
                details={"repair_id": query.repair_id},
            )

        dynamograms = await self._build_dynamograms(analytics.id)
        spos = await self._build_spos(analytics.id)

        error_screens = await self._build_error_screens(analytics.id)
        overall = await self.overall_ai_repository.get_by_analytics_id(analytics.id)
        transports = await self._build_transports(repair.id)

        return RepairAnalyticsViewDTO(
            analytics_id=analytics.id,
            repair_id=analytics.repair_id,
            is_finalized=analytics.is_finalized,
            repair_docs_id=analytics.repair_docs_id,
            summary_id=analytics.summary_id,
            dynamograms=dynamograms,
            spos=spos,
            error_screens=error_screens,
            overall_ai_analysis=self._build_overall(overall),
            transports=transports,
        )

    async def _build_transports(
        self,
        repair_id: int,
    ) -> list[RepairTransportViewDTO]:
        rows = await self.transport_repository.list_by_repair_id(repair_id)
        return [RepairTransportViewDTO.model_validate(row) for row in rows]

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

    async def _build_spos(self, analytics_id: int) -> list[SPOWithAIResultDTO]:
        link = await self.analytics_spo_repository.get_by_analytics_id(analytics_id)
        if link is None:
            return []
        spo = await self.spo_repository.get_by_id(link.spo_id)
        if spo is None:
            return []
        dto = SPOWithAIResultDTO.model_validate(spo)
        start_time, end_time = await self._load_spo_times(spo.notes_file_id)
        dto.start_time = start_time
        dto.end_time = end_time
        dto.passport = await self._load_spo_passport(spo.passport_file_id)
        ai_results = await self.spo_ai_repository.list_by_spo_ids([spo.id])
        if ai_results:
            dto.ai_result = AIResultDTO.model_validate(ai_results[0])
        return [dto]

    async def _load_spo_times(
        self,
        notes_file_id: int | None,
    ) -> tuple[datetime | None, datetime | None]:
        """Read start/end from the SPO notes.json in S3."""

        data = await self._load_json_file(notes_file_id, "notes")
        if data is None:
            return None, None
        return _parse_iso(data.get("start")), _parse_iso(data.get("end"))

    async def _load_spo_passport(
        self,
        passport_file_id: int | None,
    ) -> SPOPassportDTO | None:
        """Read passport JSON from S3 and coerce into a typed DTO."""

        data = await self._load_json_file(passport_file_id, "passport")
        if data is None:
            return None
        try:
            return SPOPassportDTO.model_validate(data)
        except ValueError:
            logger.exception(
                "SPO passport file id=%s does not match expected shape; skipping.",
                passport_file_id,
            )
            return None

    async def _load_json_file(
        self,
        file_id: int | None,
        kind: str,
    ) -> dict | None:
        """Download and parse a JSON side-artifact by File.id.

        Returns ``None`` on any failure (missing row, S3 miss, malformed
        JSON, decode error). The view should never fail because of a stale
        or missing side-artifact.
        """

        if file_id is None:
            return None
        try:
            file_row = await self.file_repository.get_by_id(file_id)
            if file_row is None or not file_row.file:
                return None
            payload = await self.storage.download_file(file_row.file)
            data = json.loads(payload.getvalue().decode("utf-8"))
        except Exception:
            logger.exception(
                "Failed to read SPO %s file id=%s; skipping.",
                kind,
                file_id,
            )
            return None
        if not isinstance(data, dict):
            return None
        return data

    @staticmethod
    def _build_overall(overall) -> OverallAIResultDTO | None:  # noqa: ANN001
        if overall is None:
            return None
        result = overall.result or {}
        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        verdict = None
        if isinstance(parsed, dict):
            try:
                verdict = OverallAIVerdictDTO.model_validate(parsed)
            except ValueError:
                verdict = None
        return OverallAIResultDTO(
            id=overall.id,
            status=overall.status,
            model_name=overall.model_name,
            prompt_version=overall.prompt_version,
            error=overall.error,
            processed_at=overall.processed_at,
            verdict=verdict,
            raw=raw if isinstance(raw, str) else None,
        )

    async def _build_error_screens(
        self,
        _analytics_id: int,
    ) -> list[BrigadeErrorScreenDTO]:
        # links = (
        #     await self.analytics_brigade_error_screen_repository.list_by_analytics_id(
        #         _analytics_id,
        #     )
        # )
        # cm_screen_ids = [link.cm_screen_id for link in links]
        cm_screen_ids = [55364, 55362, 55358]
        screens = await self.cm_brigade_error_screen_repository.list_by_ids(
            cm_screen_ids,
        )
        return [
            BrigadeErrorScreenDTO.model_validate(screen).model_copy(
                update={"screen_url": self._build_screen_url(screen.screen)},
            )
            for screen in screens
        ]

    def _build_screen_url(self, screen: str | None) -> str | None:
        if not screen:
            return None
        return f"{self.cm_media_url_header.rstrip('/')}/{screen.lstrip('/')}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
