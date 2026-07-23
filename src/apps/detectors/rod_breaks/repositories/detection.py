from collections.abc import Sequence

from apps.detectors.rod_breaks.dto.internal.repositories.detection import (
    CreateRodBreakDetectionDTO,
    CreateRodBreakRunDTO,
    UpdateRodBreakDetectionDTO,
    UpdateRodBreakRunDTO,
)
from apps.detectors.rod_breaks.models.detection import (
    RodBreakDetection,
    RodBreakRun,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RodBreakRunRepository(
    AsyncAlchemyRepository[
        CreateRodBreakRunDTO,
        UpdateRodBreakRunDTO,
        RodBreakRun,
    ],
):
    model = RodBreakRun


class RodBreakDetectionRepository(
    AsyncAlchemyRepository[
        CreateRodBreakDetectionDTO,
        UpdateRodBreakDetectionDTO,
        RodBreakDetection,
    ],
):
    model = RodBreakDetection

    async def list_filtered(
        self,
        well_id: int | None,
        *,
        only_fired: bool,
        limit: int,
    ) -> Sequence[RodBreakDetection]:
        filters = []
        if well_id is not None:
            filters.append(RodBreakDetection.well_id == well_id)
        if only_fired:
            filters.append(RodBreakDetection.fired.is_(True))

        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(RodBreakDetection.id.desc(),),
                limit=limit,
            ),
        )
