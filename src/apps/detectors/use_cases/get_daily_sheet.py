"""Суточная ведомость по запросу: сохранённая за дату или собранная сейчас.

Сборка без LLM укладывается в секунды, поэтому при отсутствии ведомости она
делается прямо в запросе и сохраняется; следующий запрос отдаёт готовое.
"""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.daily_sheet import (
    DailySheetCoverageDTO,
    DailySheetDTO,
)
from apps.detectors.dto.queries.daily_sheet import GetDailySheetQuery
from apps.detectors.models.daily_sheet import (
    DAILY_SHEET_STATUS_COMPLETED,
    DetectorDailySheet,
)
from apps.detectors.repositories import DetectorRepository
from apps.detectors.repositories.daily_sheet import DetectorDailySheetRepository
from apps.detectors.services.daily_sheet.builder import DailySheetBuilder, SheetTarget
from apps.detectors.services.daily_sheet.config import CONFIG_VERSION
from apps.detectors.services.daily_sheet.errors import DailySheetNgduNotFoundError
from apps.files.services.file import FileService
from apps.org.repositories.org import OrgRepository
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.s3.storage import AiobotoFileStorage

# В org.name_ru НГДУ идёт с префиксом («НГДУ-Кайнармунайгаз»), в бланке — без.
_NGDU_PREFIX = re.compile(r"^НГДУ[\s\-–—]*", re.IGNORECASE)
_CONNECTED_NGDU_ABAI_IDS = frozenset(item.value for item in AbaiNGDUIDsEnum)


def display_ngdu_name(name: str) -> str:
    return _NGDU_PREFIX.sub("", name).strip() or name


class GetDailySheetUseCase:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: AiobotoFileStorage,
    ) -> None:
        self.session = session
        self.storage = storage

    async def execute(self, query: GetDailySheetQuery) -> DailySheetDTO:
        target = await self._resolve_target(query)
        sheet_repo = DetectorDailySheetRepository(self.session)
        stored = (
            None
            if query.rebuild
            else await sheet_repo.get(
                detector_code=target.detector_code,
                abai_ngdu_id=target.abai_ngdu_id,
                sheet_date=target.sheet_date,
            )
        )
        if stored is not None and self._is_reusable(stored):
            detector = await DetectorRepository(self.session).get_by_code(
                target.detector_code,
            )
            sheet = self._from_stored(
                stored,
                target,
                detector.name_ru if detector else None,
            )
        else:
            sheet = await DailySheetBuilder(self.session, storage=self.storage).build(
                target,
            )

        if sheet.file_id is not None:
            info = await FileService(self.session, self.storage).get_download_info(
                sheet.file_id,
                expires_in=query.expires_in,
            )
            sheet.download_url, sheet.expires_at = info.download_url, info.expires_at
        return sheet

    async def _resolve_target(self, query: GetDailySheetQuery) -> SheetTarget:
        org = await OrgRepository(self.session).get_by_id(query.ngdu_id)
        if org is None or org.abai_id not in _CONNECTED_NGDU_ABAI_IDS:
            raise DailySheetNgduNotFoundError(details={"ngdu_id": query.ngdu_id})
        return SheetTarget(
            detector_code=query.detector_code,
            ngdu_id=org.id,
            ngdu_name=display_ngdu_name(org.name_ru),
            abai_ngdu_id=org.abai_id,
            sheet_date=query.sheet_date,
        )

    @staticmethod
    def _is_reusable(stored: DetectorDailySheet) -> bool:
        """Готовая ведомость текущей версии порогов; failed и старые — пересобрать."""
        return (
            stored.status == DAILY_SHEET_STATUS_COMPLETED
            and stored.file_id is not None
            and stored.config_version == CONFIG_VERSION
        )

    @staticmethod
    def _from_stored(
        stored: DetectorDailySheet,
        target: SheetTarget,
        detector_name: str | None,
    ) -> DailySheetDTO:
        content = stored.content or {}
        return DailySheetDTO(
            detector_code=stored.detector_code,
            detector_name_ru=detector_name,
            ngdu_id=target.ngdu_id,
            ngdu_name=target.ngdu_name,
            abai_ngdu_id=stored.abai_ngdu_id,
            sheet_date=stored.sheet_date,
            status=stored.status,
            rows_count=stored.rows_count,
            coverage=(
                DailySheetCoverageDTO.model_validate(stored.coverage)
                if stored.coverage
                else None
            ),
            built_at=stored.built_at,
            config_version=stored.config_version,
            file_id=stored.file_id,
            error=stored.error,
            rebuilt=False,
            top=content.get("top", []),
            attention=content.get("attention", []),
            rows=content.get("rows", []),
            notes=content.get("notes", []),
        )
