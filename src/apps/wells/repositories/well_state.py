from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from apps.wells.dto.internal.repositories.well_state import (
    CreateWellStateDTO,
    UpdateWellStateDTO,
    UpsertWellDriveStateDTO,
    UpsertWellStateRunDTO,
)
from apps.wells.models.well_state import WellDriveState, WellState, WellStateRun
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class WellStateRepository(
    AsyncAlchemyRepository[CreateWellStateDTO, UpdateWellStateDTO, WellState],
):
    model = WellState

    async def list_open_by_well_ids(
        self,
        well_ids: Sequence[int],
    ) -> dict[int, WellState]:
        """Открытая строка (текущее состояние) на скважину."""
        if not well_ids:
            return {}
        rows = await self.get_list(
            QuerySpec(
                filters=(
                    WellState.well_id.in_(well_ids),
                    WellState.ended_at.is_(None),
                ),
            ),
        )
        return {row.well_id: row for row in rows}

    async def list_open_refs(self) -> Sequence[tuple[int, int, int]]:
        """``(id, well_id, abai_ngdu_id)`` всех открытых строк — по частичному
        индексу открытых строк."""
        result = await self.session.execute(
            select(WellState.id, WellState.well_id, WellState.abai_ngdu_id).where(
                WellState.ended_at.is_(None),
            ),
        )
        return [tuple(row) for row in result.all()]

    async def update_by_id(self, row_id: int, data: UpdateWellStateDTO) -> None:
        await self.update(data, filters=(WellState.id == row_id,))


class WellDriveStateRepository(
    AsyncAlchemyRepository[
        UpsertWellDriveStateDTO,
        UpsertWellDriveStateDTO,
        WellDriveState,
    ],
):
    model = WellDriveState

    async def get_map_by_station_ids(
        self,
        station_ids: Sequence[int],
    ) -> dict[int, WellDriveState]:
        if not station_ids:
            return {}
        rows = await self.get_list(
            QuerySpec(filters=(WellDriveState.station_id.in_(station_ids),)),
        )
        return {row.station_id: row for row in rows}

    async def upsert_many(self, data: Sequence[UpsertWellDriveStateDTO]) -> None:
        if not data:
            return
        stmt = insert(WellDriveState).values([item.model_dump() for item in data])
        columns = {
            name: stmt.excluded[name]
            for name in UpsertWellDriveStateDTO.model_fields
            if name != "station_id"
        }
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[WellDriveState.station_id],
                set_=columns,
            ),
        )


class WellStateRunRepository(
    AsyncAlchemyRepository[UpsertWellStateRunDTO, UpsertWellStateRunDTO, WellStateRun],
):
    model = WellStateRun

    async def get_map(self) -> dict[int, WellStateRun]:
        rows = await self.get_list(QuerySpec())
        return {row.abai_ngdu_id: row for row in rows}

    async def upsert(self, data: UpsertWellStateRunDTO) -> None:
        stmt = insert(WellStateRun).values(data.model_dump())
        columns = {
            name: stmt.excluded[name]
            for name in UpsertWellStateRunDTO.model_fields
            if name != "abai_ngdu_id"
        }
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[WellStateRun.abai_ngdu_id],
                set_=columns,
            ),
        )
