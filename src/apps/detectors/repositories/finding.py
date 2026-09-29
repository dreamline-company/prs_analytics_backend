from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, select

from apps.detectors.dto.internal.repositories.finding import (
    CreateDetectorFindingDTO,
    UpdateDetectorFindingDTO,
)
from apps.detectors.models.finding import DetectorFinding
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class DetectorFindingRepository(
    AsyncAlchemyRepository[
        CreateDetectorFindingDTO,
        UpdateDetectorFindingDTO,
        DetectorFinding,
    ],
):
    model = DetectorFinding

    async def replace_for_date(
        self,
        *,
        detector_code: str,
        fix_date: date,
        rows: Sequence[CreateDetectorFindingDTO],
    ) -> None:
        """Срез за дату пересчитывается целиком: старые строки заменяются."""
        await self.session.execute(
            delete(DetectorFinding).where(
                DetectorFinding.detector_code == detector_code,
                DetectorFinding.fix_date == fix_date,
            ),
        )
        await self.bulk_create(rows)

    async def get_last_fix_date(self, detector_code: str) -> date | None:
        result = await self.session.execute(
            select(func.max(DetectorFinding.fix_date)).where(
                DetectorFinding.detector_code == detector_code,
            ),
        )
        return result.scalar()

    async def list_for_date(
        self,
        *,
        detector_code: str,
        fix_date: date,
        kinds: Sequence[str] | None = None,
        well_id: int | None = None,
    ) -> Sequence[DetectorFinding]:
        filters = [
            DetectorFinding.detector_code == detector_code,
            DetectorFinding.fix_date == fix_date,
        ]
        if kinds is not None:
            filters.append(DetectorFinding.kind.in_(kinds))
        if well_id is not None:
            filters.append(DetectorFinding.well_id == well_id)
        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(DetectorFinding.kind, DetectorFinding.well_id),
            ),
        )
