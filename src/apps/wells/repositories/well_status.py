from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select

from apps.wells.dto.internal.repositories.well_status import (
    CreateWellStatusDTO,
    CreateWellStatusReasonDTO,
    CreateWellStatusTypeDTO,
    UpdateWellStatusDTO,
    UpdateWellStatusReasonDTO,
    UpdateWellStatusTypeDTO,
)
from apps.wells.models.well_status import (
    WellStatus,
    WellStatusReason,
    WellStatusType,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class WellStatusTypeRepository(
    AsyncAlchemyRepository[
        CreateWellStatusTypeDTO,
        UpdateWellStatusTypeDTO,
        WellStatusType,
    ],
):
    model = WellStatusType

    async def list_all(self) -> Sequence[WellStatusType]:
        return await self.get_list(
            QuerySpec(order_by=(WellStatusType.abai_id.asc(),)),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellStatusTypeDTO,
    ) -> WellStatusType:
        return await self.update(
            data=data,
            filters=(WellStatusType.abai_id == abai_id,),
        )


class WellStatusReasonRepository(
    AsyncAlchemyRepository[
        CreateWellStatusReasonDTO,
        UpdateWellStatusReasonDTO,
        WellStatusReason,
    ],
):
    model = WellStatusReason

    async def list_all(self) -> Sequence[WellStatusReason]:
        return await self.get_list(
            QuerySpec(order_by=(WellStatusReason.abai_id.asc(),)),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellStatusReasonDTO,
    ) -> WellStatusReason:
        return await self.update(
            data=data,
            filters=(WellStatusReason.abai_id == abai_id,),
        )


class WellStatusRepository(
    AsyncAlchemyRepository[CreateWellStatusDTO, UpdateWellStatusDTO, WellStatus],
):
    model = WellStatus

    async def get_max_abai_id(self) -> int:
        result = await self.session.execute(select(func.max(WellStatus.abai_id)))
        return result.scalar() or 0

    async def list_open_intervals(self, now: datetime) -> Sequence[WellStatus]:
        """Локальные незакрытые интервалы: dend в будущем (UTC, как в источнике)."""
        return await self.get_list(
            QuerySpec(
                filters=(WellStatus.dend > now,),
                order_by=(WellStatus.abai_id.asc(),),
            ),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellStatusDTO,
    ) -> WellStatus:
        return await self.update(
            data=data,
            filters=(WellStatus.abai_id == abai_id,),
        )

    async def delete_by_abai_ids(self, abai_ids: Sequence[int]) -> None:
        if not abai_ids:
            return
        await self.session.execute(
            delete(WellStatus).where(WellStatus.abai_id.in_(abai_ids)),
        )

    async def list_intervals_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
        *,
        since: datetime,
        until: datetime,
    ) -> Sequence[tuple[int, datetime, datetime, str | None, str, str | None]]:
        """Интервалы скважин, пересекающие [since, until), с кодом и причиной.

        Строка: (abai_well_id, dbeg, dend, код статуса, название статуса,
        название причины). Время — UTC, как в источнике.
        """
        if not abai_well_ids:
            return ()

        stmt = (
            select(
                WellStatus.abai_well_id,
                WellStatus.dbeg,
                WellStatus.dend,
                WellStatusType.code,
                WellStatusType.name_ru,
                WellStatusReason.name_ru,
            )
            .join(WellStatusType, WellStatusType.abai_id == WellStatus.status)
            .join(
                WellStatusReason,
                WellStatusReason.abai_id == WellStatus.reason,
                isouter=True,
            )
            .where(
                WellStatus.abai_well_id.in_(abai_well_ids),
                WellStatus.dbeg < until,
                WellStatus.dend >= since,
            )
            .order_by(WellStatus.abai_well_id, WellStatus.dbeg, WellStatus.abai_id)
        )
        result = await self.session.execute(stmt)
        return [tuple(row) for row in result.all()]
