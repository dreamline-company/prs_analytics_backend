"""Подбор доноров под стоящие скважины — раз в час, по каждому НГДУ пула.

1. Скважины НГДУ (у Кайнармунайгаза — только VMB), кто из них стоит, план Qн.
2. Открытые пары закрываются, если скважина с потерей запустилась или донор
   встал (простой, ремонт, авария). Остальные пары не перетасовываются.
3. Непокрытые части потерь добираются свободными донорами — ``allocate``.

Каждый НГДУ — своя транзакция: сбой одного не мешает остальным.

    python -m apps.compensation.tasks.assign_donors.assign_donors
"""

import asyncio
from collections import defaultdict
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.compensation.constants import RECOMMENDATION_PENDING
from apps.compensation.dto.internal.repositories.compensation import (
    CreateCompensationRecommendationDTO,
)
from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationRecommendationRepository,
)
from apps.compensation.services.allocation import (
    DonorInput,
    LossInput,
    OpenPair,
    allocate,
    pairs_to_close,
    target_speed,
)
from apps.compensation.services.state import (
    CompensationStateService,
    to_local,
    utc_now,
)
from apps.models_registry import *  # noqa: F403
from apps.org.repositories.org import OrgRepository
from apps.org.services import well_name_prefix
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


class DonorAssigner:
    def __init__(self, session: AsyncSession, *, now: datetime | None = None) -> None:
        self.session = session
        self.now = now or utc_now()  # наивный UTC — как время статусов ABAI
        self.state_service = CompensationStateService(session)
        self.donor_repository = CompensationDonorRepository(session)
        self.pair_repository = CompensationRecommendationRepository(session)

    async def run(self) -> None:
        orgs = await OrgRepository(self.session).list_by_abai_ids(
            await self.donor_repository.list_ngdu_ids(),
        )
        # Снимок до цикла: после rollback ORM-объекты протухают.
        ngdu_ids = [org.id for org in orgs]
        for ngdu_id in ngdu_ids:
            try:
                opened, closed = await self._run_ngdu(ngdu_id)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception("Compensation assign ngdu_id=%s failed", ngdu_id)
                continue
            logger.info(
                "Compensation ngdu_id=%s: opened=%s, closed=%s",
                ngdu_id,
                opened,
                closed,
            )

    async def _run_ngdu(self, ngdu_id: int) -> tuple[int, int]:
        wells = {
            well.id: well for well in await self.state_service.scope_wells(ngdu_id)
        }
        state = await self.state_service.load(list(wells.values()), now=self.now)
        donors = await self.donor_repository.list_by_well_ids(list(wells))
        donor_by_id = {donor.id: donor for donor in donors}
        available = {
            donor.well_id
            for donor in donors
            if donor.well_id not in state.stops and donor.well_id not in state.alarms
        }
        opened_at = to_local(self.now)

        open_pairs = await self.pair_repository.list_open_by_donor_ids(
            list(donor_by_id),
        )
        closing = pairs_to_close(
            [
                OpenPair(pair.id, pair.loss_well_id, donor_by_id[pair.donor_id].well_id)
                for pair in open_pairs
            ],
            stopped_well_ids=set(state.stops),
            available_donor_well_ids=available,
        )
        for pair_id, reason in closing:
            await self.pair_repository.close(
                pair_id,
                closed_at=opened_at,
                reason=reason,
            )

        closed_ids = {pair_id for pair_id, _ in closing}
        covered_by_loss: dict[int, float] = defaultdict(float)
        busy: set[int] = set()
        for pair in open_pairs:
            if pair.id not in closed_ids:
                covered_by_loss[pair.loss_well_id] += pair.gain
                busy.add(pair.donor_id)

        loss_wells = [
            wells[well_id]
            for well_id in state.stops
            if well_id in state.plans and well_id in wells
        ]
        free = [
            donor
            for donor in donors
            if donor.well_id in available and donor.id not in busy
        ]
        points = await self.state_service.points(
            [*loss_wells, *(wells[donor.well_id] for donor in free)],
        )
        new_pairs = allocate(
            [
                LossInput(
                    well.id,
                    well_name_prefix(well.name) or "",
                    state.plans[well.id][0],
                    covered_by_loss[well.id],
                    points[well.id],
                )
                for well in loss_wells
            ],
            [
                DonorInput(
                    donor.id,
                    well_name_prefix(wells[donor.well_id].name) or "",
                    donor.gain,
                    donor.risk,
                    points[donor.well_id],
                )
                for donor in free
            ],
            excluded=await self.pair_repository.list_rejected_pairs(
                [well.id for well in loss_wells],
            ),
        )
        await self.pair_repository.bulk_create(
            [
                CreateCompensationRecommendationDTO(
                    loss_well_id=pair.loss_well_id,
                    donor_id=pair.donor_id,
                    status=RECOMMENDATION_PENDING,
                    loss=state.plans[pair.loss_well_id][0],
                    gain=donor_by_id[pair.donor_id].gain,
                    speed_from=donor_by_id[pair.donor_id].speed,
                    speed_to=target_speed(
                        donor_by_id[pair.donor_id].speed,
                        donor_by_id[pair.donor_id].step_percent,
                    ),
                    distance_m=pair.distance_m,
                    opened_at=opened_at,
                )
                for pair in new_pairs
            ],
        )
        return len(new_pairs), len(closing)


async def main() -> None:
    async with session_makers["app"]() as session:
        await DonorAssigner(session).run()


@celery_app.task(name="compensation.assign_donors")
def assign_compensation_donors() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
