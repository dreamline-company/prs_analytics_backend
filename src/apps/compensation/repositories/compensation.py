from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.compensation.constants import (
    RECOMMENDATION_PENDING,
    RECOMMENDATION_REJECTED,
)
from apps.compensation.dto.internal.repositories.compensation import (
    CreateCompensationDonorDTO,
    CreateCompensationLossDTO,
    CreateCompensationRecommendationDTO,
    UpdateCompensationDonorDTO,
    UpdateCompensationLossDTO,
    UpdateCompensationRecommendationDTO,
)
from apps.compensation.models.compensation import (
    CompensationDonor,
    CompensationLoss,
    CompensationRecommendation,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class CompensationDonorRepository(
    AsyncAlchemyRepository[
        CreateCompensationDonorDTO,
        UpdateCompensationDonorDTO,
        CompensationDonor,
    ],
):
    model = CompensationDonor

    async def upsert(self, data: CreateCompensationDonorDTO) -> None:
        """Донор по скважине: новый — вставить, известный — перезаписать."""
        values = data.model_dump()
        stmt = pg_insert(CompensationDonor).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["well_id"],
            set_={key: stmt.excluded[key] for key in values if key != "well_id"},
        )
        await self.session.execute(stmt)

    async def list_by_well_ids(
        self,
        well_ids: Sequence[int],
        *,
        in_pool_only: bool = False,
    ) -> Sequence[CompensationDonor]:
        """``in_pool_only`` — только участвующие в подборе (прошли критерии и
        есть телеметрия)."""
        if not well_ids:
            return ()
        filters = [CompensationDonor.well_id.in_(well_ids)]
        if in_pool_only:
            filters.append(CompensationDonor.in_pool.is_(True))
        return await self.get_list(
            QuerySpec(filters=tuple(filters), order_by=(CompensationDonor.id,)),
        )

    async def mark_out_of_pool(
        self,
        source_rows: dict[int, dict],
        *,
        calculated_at: datetime,
    ) -> None:
        """Скважины, не прошедшие критерии: вне пула, причины — в source_row."""
        for well_id, source_row in source_rows.items():
            await self.session.execute(
                update(CompensationDonor)
                .where(CompensationDonor.well_id == well_id)
                .values(
                    in_pool=False,
                    calculated_at=calculated_at,
                    source_row=source_row,
                ),
            )

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[CompensationDonor]:
        if not ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(CompensationDonor.id.in_(ids),)),
        )

    async def list_ngdu_ids(self) -> list[int]:
        result = await self.session.execute(
            select(CompensationDonor.abai_ngdu_id).distinct(),
        )
        return sorted(result.scalars().all())


class CompensationRecommendationRepository(
    AsyncAlchemyRepository[
        CreateCompensationRecommendationDTO,
        UpdateCompensationRecommendationDTO,
        CompensationRecommendation,
    ],
):
    model = CompensationRecommendation

    async def list_open_by_donor_ids(
        self,
        donor_ids: Sequence[int],
    ) -> Sequence[CompensationRecommendation]:
        if not donor_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(
                    CompensationRecommendation.donor_id.in_(donor_ids),
                    CompensationRecommendation.closed_at.is_(None),
                ),
                order_by=(CompensationRecommendation.id,),
            ),
        )

    async def list_open_by_loss_well_ids(
        self,
        loss_well_ids: Sequence[int],
    ) -> Sequence[CompensationRecommendation]:
        if not loss_well_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(
                    CompensationRecommendation.loss_well_id.in_(loss_well_ids),
                    CompensationRecommendation.closed_at.is_(None),
                ),
                order_by=(CompensationRecommendation.id,),
            ),
        )

    async def list_latest_by_donor_ids(
        self,
        donor_ids: Sequence[int],
    ) -> dict[int, CompensationRecommendation]:
        """Последняя пара каждого донора: открытая или последняя закрытая."""
        if not donor_ids:
            return {}
        stmt = (
            select(CompensationRecommendation)
            .where(CompensationRecommendation.donor_id.in_(donor_ids))
            .distinct(CompensationRecommendation.donor_id)
            .order_by(
                CompensationRecommendation.donor_id,
                CompensationRecommendation.closed_at.desc().nullsfirst(),
                CompensationRecommendation.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return {row.donor_id: row for row in result.scalars()}

    async def list_rejected_pairs(
        self,
        loss_well_ids: Sequence[int],
    ) -> frozenset[tuple[int, int]]:
        """(скважина потери, донор), которые технолог отклонял."""
        if not loss_well_ids:
            return frozenset()
        result = await self.session.execute(
            select(
                CompensationRecommendation.loss_well_id,
                CompensationRecommendation.donor_id,
            ).where(
                CompensationRecommendation.loss_well_id.in_(loss_well_ids),
                CompensationRecommendation.status == RECOMMENDATION_REJECTED,
            ),
        )
        return frozenset((row[0], row[1]) for row in result.all())

    async def close(
        self,
        pair_id: int,
        *,
        closed_at: datetime,
        reason: str,
        pending_only: bool = False,
    ) -> bool:
        """Закрыть открытую пару; ``pending_only`` — только если она всё ещё
        «на согласовании» (перехват не трогает принятые). True — закрыта."""
        filters = [
            CompensationRecommendation.id == pair_id,
            CompensationRecommendation.closed_at.is_(None),
        ]
        if pending_only:
            filters.append(CompensationRecommendation.status == RECOMMENDATION_PENDING)
        result = await self.session.execute(
            update(CompensationRecommendation)
            .where(*filters)
            .values(closed_at=closed_at, close_reason=reason),
        )
        return result.rowcount > 0


class CompensationLossRepository(
    AsyncAlchemyRepository[
        CreateCompensationLossDTO,
        UpdateCompensationLossDTO,
        CompensationLoss,
    ],
):
    model = CompensationLoss

    async def replace_for_wells(
        self,
        well_ids: Sequence[int],
        rows: Sequence[CreateCompensationLossDTO],
    ) -> None:
        """Потери скважин ``well_ids`` целиком: прежние строки заменяются."""
        if well_ids:
            await self.session.execute(
                delete(CompensationLoss).where(CompensationLoss.well_id.in_(well_ids)),
            )
        await self.bulk_create(rows)

    async def list_by_well_ids(
        self,
        well_ids: Sequence[int],
    ) -> Sequence[CompensationLoss]:
        if not well_ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(CompensationLoss.well_id.in_(well_ids),)),
        )
