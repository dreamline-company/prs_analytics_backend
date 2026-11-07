"""Builds the frontend view of one repair's analytics.

Aggregates data from the app DB (analytics, dynamograms, SPOs, AI results)
and the CM DB (brigade error screens referenced by ``cm_screen_id``).
"""

import json
from collections.abc import Sequence
from datetime import datetime

from starlette import status

from apps.files.models.file import File
from apps.files.repositories.file import FileRepository
from apps.org.dto.internal.brigade import BrigadeShortDTO
from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.repairs.dto.internal.analytics_view import (
    AIResultDTO,
    BrigadeErrorScreenDTO,
    DynamogramsPairDTO,
    DynamogramWithAIResultDTO,
    OverallAIResultDTO,
    OverallAIVerdictDTO,
    RepairAnalyticsOverallInfoDTO,
    RepairAnalyticsViewDTO,
    RepairMetaDTO,
    RepairTransportViewDTO,
    SPOPassportDTO,
    SPOWithAIResultDTO,
)
from apps.repairs.dto.queries.analytics_view import GetRepairAnalyticsViewQuery
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.analytics import (
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.repositories.transport import RepairTransportRepository
from apps.repairs.services.error_screens import list_repair_error_screens
from apps.repairs.tasks.fetch_sources.candidates import local_now, repair_window
from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.repositories import WellRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage
from shared.errors import HttpError
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

logger = get_logger(__name__)

# Срок presigned-ссылок на файлы СПО — как у /files/{id} по умолчанию.
_SPO_URL_TTL_SEC = 3600


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
        repair_brigade_repository: RepairBrigadeRepository,
        unique_brigade_repository: UniqueBrigadeRepository,
        analytics_repository: RepairAnalyticsRepository,
        analytics_dynamogram_repository: RepairAnalyticsDynamogramRepository,
        dynamogram_repository: DynamogramRepository,
        spo_repository: SPORepository,
        dynamogram_ai_repository: RepairDynamogramAIResultRepository,
        spo_ai_repository: RepairSPOAIResultRepository,
        overall_ai_repository: RepairAIAnalysisRepository,
        transport_repository: RepairTransportRepository,
        file_repository: FileRepository,
        storage: AiobotoFileStorage,
        cm_brigade_repository: CMBrigadeRepository,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
        cm_media_url_header: str,
    ) -> None:
        self.repair_repository = repair_repository
        self.wells_repository = wells_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.unique_brigade_repository = unique_brigade_repository
        self.analytics_repository = analytics_repository
        self.analytics_dynamogram_repository = analytics_dynamogram_repository
        self.dynamogram_repository = dynamogram_repository
        self.spo_repository = spo_repository
        self.dynamogram_ai_repository = dynamogram_ai_repository
        self.spo_ai_repository = spo_ai_repository
        self.overall_ai_repository = overall_ai_repository
        self.transport_repository = transport_repository
        self.file_repository = file_repository
        self.storage = storage
        self.cm_brigade_repository = cm_brigade_repository
        self.cm_brigade_error_screen_repository = cm_brigade_error_screen_repository
        self.cm_media_url_header = cm_media_url_header

    async def execute(
        self,
        query: GetRepairAnalyticsViewQuery,
    ) -> RepairAnalyticsViewDTO:
        repair = await self.repair_repository.get_by_id(query.repair_id)
        if repair is None:
            raise RepairAnalyticsNotFoundError(
                details={"repair_id": query.repair_id},
            )
        well = await self.wells_repository.get_by_abai_id(abai_id=repair.abai_well_id)
        if well is None:
            raise RepairAnalyticsNotFoundError(
                details={"well_no_exists": repair.abai_well_id},
            )
        analytics = await self.analytics_repository.get_by_repair_id(query.repair_id)
        if analytics is None:
            raise RepairAnalyticsNotFoundError(
                details={"repair_id": query.repair_id},
            )

        brigade = None
        brigade_link = await self.repair_brigade_repository.get_by_repair_id(repair.id)
        if brigade_link is not None:
            unique_brigade = await self.unique_brigade_repository.get_by_id(
                brigade_link.brigade_id,
            )
            if unique_brigade is not None:
                brigade = BrigadeShortDTO.model_validate(unique_brigade)

        overall_info = RepairAnalyticsOverallInfoDTO(
            well=WellShortDTO.model_validate(well),
            brigade=brigade,
            repair=RepairMetaDTO.model_validate(repair),
        )

        dynamograms = await self._build_dynamograms(analytics.id)
        spos = await self._build_spos(repair, well.id)

        error_screens = await self._build_error_screens(repair)
        overall = await self.overall_ai_repository.get_by_analytics_id(analytics.id)
        transports = await self._build_transports(repair.id)

        return RepairAnalyticsViewDTO(
            analytics_id=analytics.id,
            repair_id=analytics.repair_id,
            is_finalized=analytics.is_finalized,
            repair_docs_id=analytics.repair_docs_id,
            summary_id=analytics.summary_id,
            overall_info=overall_info,
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

    async def _build_spos(
        self,
        repair: Repair,
        well_id: int,
    ) -> list[SPOWithAIResultDTO]:
        """Все СПО скважины в окне ремонта по времени.

        Окно то же, что у привязки замеров и ИИ-вердикта: [начало, конец
        (у открытого — сейчас) + сутки].
        """
        start, end = repair_window(repair, now=local_now())
        spos = await self.spo_repository.list_by_well_id_in_window(well_id, start, end)
        if not spos:
            return []
        ai_results = await self.spo_ai_repository.list_by_spo_ids(
            [spo.id for spo in spos],
        )
        ai_by_spo_id = {result.spo_id: result for result in ai_results}
        file_ids = {
            file_id
            for spo in spos
            for file_id in (
                spo.file_id,
                spo.chart_file_id,
                spo.chart_json_file_id,
                spo.notes_file_id,
                spo.passport_file_id,
            )
            if file_id is not None
        }
        files = {
            row.id: row
            for row in await self.file_repository.list_by_ids(sorted(file_ids))
            if row.file
        }
        side_json = await self._load_json_files(
            [
                files[file_id]
                for spo in spos
                for file_id in (spo.notes_file_id, spo.passport_file_id)
                if file_id in files
            ],
        )
        urls = await self._presigned_urls(list(files.values()))

        dtos: list[SPOWithAIResultDTO] = []
        for spo in spos:
            dto = SPOWithAIResultDTO.model_validate(spo)
            spo_notes = side_json.get(spo.notes_file_id) or {}
            dto.start_time = _parse_iso(spo_notes.get("start"))
            dto.end_time = _parse_iso(spo_notes.get("end"))
            row_count = spo_notes.get("row_count")
            dto.row_count = row_count if isinstance(row_count, int) else None
            dto.passport = _parse_passport(
                side_json.get(spo.passport_file_id),
                spo.passport_file_id,
            )
            dto.file_url = urls.get(spo.file_id)
            dto.chart_file_url = urls.get(spo.chart_file_id)
            dto.chart_json_file_url = urls.get(spo.chart_json_file_id)
            dto.notes_file_url = urls.get(spo.notes_file_id)
            dto.passport_file_url = urls.get(spo.passport_file_id)
            ai = ai_by_spo_id.get(spo.id)
            if ai is not None:
                dto.ai_result = AIResultDTO.model_validate(ai)
            dtos.append(dto)
        return dtos

    async def _load_json_files(self, rows: Sequence[File]) -> dict[int, dict]:
        """File.id → JSON сопутствующих файлов СПО (паспорт, notes.json) из S3.

        Непрочитанное (нет в S3, битый JSON) пропускается: страница не должна
        падать из-за устаревшего или пропавшего файла.
        """
        if not rows:
            return {}
        try:
            payloads = await self.storage.download_files(row.file for row in rows)
        except Exception:
            logger.exception("Failed to read SPO side files from S3; skipping.")
            return {}

        result: dict[int, dict] = {}
        for row in rows:
            payload = payloads.get(row.file)
            if payload is None:
                continue
            try:
                data = json.loads(payload.getvalue().decode("utf-8"))
            except ValueError:
                logger.exception("SPO file id=%s is not JSON; skipping.", row.id)
                continue
            if isinstance(data, dict):
                result[row.id] = data
        return result

    async def _presigned_urls(self, rows: Sequence[File]) -> dict[int, str]:
        """File.id → presigned-ссылка: фронту не нужен /files/{id} на каждый файл."""
        if not rows:
            return {}
        try:
            urls = await self.storage.generate_presigned_urls(
                (row.file for row in rows),
                expires_in=_SPO_URL_TTL_SEC,
            )
        except Exception:
            logger.exception("Failed to presign SPO files; skipping.")
            return {}
        return {row.id: urls[row.file] for row in rows if row.file in urls}

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
        repair: Repair,
    ) -> list[BrigadeErrorScreenDTO]:
        """Экраны бригады за время ремонта — та же цепочка, что в KPI и
        ИИ-вердикте, но только обработанные в CM (``is_processed``)."""
        screens = await list_repair_error_screens(
            repair,
            repair_brigade_repo=self.repair_brigade_repository,
            unique_brigade_repo=self.unique_brigade_repository,
            cm_brigade_repo=self.cm_brigade_repository,
            cm_brigade_error_screen_repo=self.cm_brigade_error_screen_repository,
            processed_only=True,
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


def _parse_passport(
    data: dict | None,
    file_id: int | None,
) -> SPOPassportDTO | None:
    if data is None:
        return None
    try:
        return SPOPassportDTO.model_validate(data)
    except ValueError:
        logger.exception(
            "SPO passport file id=%s does not match expected shape; skipping.",
            file_id,
        )
        return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
