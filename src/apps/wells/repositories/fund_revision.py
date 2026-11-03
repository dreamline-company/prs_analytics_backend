from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, select

from apps.wells.dto.internal.repositories.fund_revision import (
    CreateFundRevisionDTO,
    UpdateFundRevisionDTO,
)
from apps.wells.models.fund_revision import FundRevision
from shared.repository.sqlalchemy import AsyncAlchemyRepository


class FundRevisionRepository(
    AsyncAlchemyRepository[
        CreateFundRevisionDTO,
        UpdateFundRevisionDTO,
        FundRevision,
    ],
):
    model = FundRevision

    async def replace_revision(
        self,
        revision_date: date,
        rows: Sequence[CreateFundRevisionDTO],
    ) -> None:
        """Ревизия на дату целиком: прежние строки той же даты заменяются."""
        await self.session.execute(
            delete(FundRevision).where(FundRevision.revision_date == revision_date),
        )
        await self.bulk_create(rows)

    async def get_latest_by_well_ids(
        self,
        well_ids: Sequence[int],
    ) -> dict[int, FundRevision]:
        """Строка последней ревизии, в которой есть скважина."""
        if not well_ids:
            return {}
        result = await self.session.execute(
            select(FundRevision)
            .where(FundRevision.well_id.in_(well_ids))
            .order_by(FundRevision.well_id, FundRevision.revision_date.desc())
            .distinct(FundRevision.well_id),
        )
        return {row.well_id: row for row in result.scalars()}
