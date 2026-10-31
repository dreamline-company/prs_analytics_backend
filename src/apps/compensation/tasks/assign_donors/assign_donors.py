"""Подбор доноров под стоящие скважины — раз в час, по каждому НГДУ пула.

1. Скважины НГДУ (у Кайнармунайгаза — только VMB), кто из них стоит, план Qн.
2. Открытые пары закрываются, если скважина с потерей запустилась или донор
   встал (простой, ремонт, авария), а пары «на согласовании» сверх лимита
   доноров на потерю — тоже.
3. Непокрытые части потерь добираются свободными донорами, а потери, стоящие
   не меньше ``PREEMPT_AFTER_HOURS``, перехватывают доноров «на согласовании» у
   потерь меньше в ``PREEMPT_RATIO`` раза — ``allocate``. Перехваченные пары
   закрываются с причиной ``donor_preempted``.

Расчёт отделён от записи: ``--dry-run`` печатает изменения и ничего не пишет
(транзакция только на чтение). Каждый НГДУ — своя транзакция: сбой одного не
мешает остальным.

    python -m apps.compensation.tasks.assign_donors.assign_donors [--dry-run]
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.compensation.constants import (
    CLOSE_DONOR_PREEMPTED,
    PREEMPT_AFTER_HOURS,
    RECOMMENDATION_PENDING,
)
from apps.compensation.dto.internal.repositories.compensation import (
    CreateCompensationRecommendationDTO,
)
from apps.compensation.models.compensation import (
    CompensationDonor,
    CompensationRecommendation,
)
from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationRecommendationRepository,
)
from apps.compensation.services.allocation import (
    Allocation,
    DonorInput,
    HeldDonor,
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
from apps.wells.models.well import Well
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


@dataclass(slots=True)
class NgduChanges:
    """Что подбор изменит в НГДУ: закрытия, перехваты и новые пары."""

    wells: dict[int, Well]
    donor_by_id: dict[int, CompensationDonor]
    open_pairs: list[CompensationRecommendation]
    plans: dict[int, tuple[float, date]]
    closing: list[tuple[int, str]]  # (пара, причина) — кроме перехватов
    allocation: Allocation


class DonorAssigner:
    def __init__(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> None:
        self.session = session
        self.now = now or utc_now()  # наивный UTC; в местное переводит state.load
        self.dry_run = dry_run
        self.state_service = CompensationStateService(session)
        self.donor_repository = CompensationDonorRepository(session)
        self.pair_repository = CompensationRecommendationRepository(session)

    async def run(self) -> None:
        orgs = await OrgRepository(self.session).list_by_abai_ids(
            await self.donor_repository.list_ngdu_ids(),
        )
        # Снимок до цикла: после rollback ORM-объекты протухают.
        ngdus = [(org.id, org.name_ru_short or org.name_ru) for org in orgs]
        await self.session.rollback()
        for ngdu_id, name in ngdus:
            try:
                if self.dry_run:
                    await self.session.execute(text("SET TRANSACTION READ ONLY"))
                changes = await self._plan(ngdu_id)
                if self.dry_run:
                    _print_changes(name, changes)
                    await self.session.rollback()
                    continue
                await self._apply(changes)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception("Compensation assign ngdu_id=%s failed", ngdu_id)
                continue
            logger.info(
                "Compensation ngdu_id=%s: opened=%s, closed=%s, preempted=%s",
                ngdu_id,
                len(changes.allocation.pairs),
                len(changes.closing),
                len(changes.allocation.preempted),
            )

    async def _plan(self, ngdu_id: int) -> NgduChanges:
        """Расчёт без записи."""
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
        local_now = to_local(self.now)

        open_pairs = list(
            await self.pair_repository.list_open_by_donor_ids(list(donor_by_id)),
        )
        closing = pairs_to_close(
            [
                OpenPair(
                    pair.id,
                    pair.loss_well_id,
                    donor_by_id[pair.donor_id].well_id,
                    pair.status,
                    donor_by_id[pair.donor_id].risk,
                    pair.distance_m,
                )
                for pair in open_pairs
            ],
            stopped_well_ids=set(state.stops),
            available_donor_well_ids=available,
        )
        closed_ids = {pair_id for pair_id, _ in closing}
        kept = [pair for pair in open_pairs if pair.id not in closed_ids]
        busy = {pair.donor_id for pair in kept}

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
        # Координаты — и доноров открытых пар: их тоже сравнивают по расстоянию.
        points = await self.state_service.points(
            [
                *loss_wells,
                *(wells[donor.well_id] for donor in free),
                *(wells[donor_by_id[pair.donor_id].well_id] for pair in kept),
            ],
        )

        def donor_input(
            donor: CompensationDonor,
            gain: float | None = None,
        ) -> DonorInput:
            return DonorInput(
                donor.id,
                well_name_prefix(wells[donor.well_id].name) or "",
                donor.gain if gain is None else gain,
                donor.risk,
                points[donor.well_id],
            )

        preempt_since = local_now - timedelta(hours=PREEMPT_AFTER_HOURS)
        allocation = allocate(
            [
                LossInput(
                    well.id,
                    well_name_prefix(well.name) or "",
                    state.plans[well.id][0],
                    points[well.id],
                    can_preempt=state.stops[well.id].since <= preempt_since,
                )
                for well in loss_wells
            ],
            [donor_input(donor) for donor in free],
            held=[
                HeldDonor(
                    pair.id,
                    pair.loss_well_id,
                    # Прирост — из пары, как «компенсировано» на экранах: пул
                    # могли перезагрузить с другими приростами.
                    donor_input(donor_by_id[pair.donor_id], pair.gain),
                    pending=pair.status == RECOMMENDATION_PENDING,
                )
                for pair in kept
            ],
            excluded=await self.pair_repository.list_rejected_pairs(
                [well.id for well in loss_wells],
            ),
        )
        return NgduChanges(
            wells=wells,
            donor_by_id=donor_by_id,
            open_pairs=open_pairs,
            plans=state.plans,
            closing=closing,
            allocation=allocation,
        )

    async def _apply(self, changes: NgduChanges) -> None:
        """Закрытия до вставок: у донора не может быть двух открытых пар."""
        closed_at = to_local(self.now)
        for pair_id, reason in changes.closing:
            await self.pair_repository.close(
                pair_id,
                closed_at=closed_at,
                reason=reason,
            )
        for pair_id in changes.allocation.preempted:
            closed = await self.pair_repository.close(
                pair_id,
                closed_at=closed_at,
                reason=CLOSE_DONOR_PREEMPTED,
                pending_only=True,
            )
            if not closed:
                # Пару успели принять или закрыть — откат НГДУ, подбор через час.
                msg = f"Preempted pair {pair_id} is no longer pending"
                raise RuntimeError(msg)
        donor_by_id = changes.donor_by_id
        await self.pair_repository.bulk_create(
            [
                CreateCompensationRecommendationDTO(
                    loss_well_id=pair.loss_well_id,
                    donor_id=pair.donor_id,
                    status=RECOMMENDATION_PENDING,
                    loss=changes.plans[pair.loss_well_id][0],
                    gain=donor_by_id[pair.donor_id].gain,
                    speed_from=donor_by_id[pair.donor_id].speed,
                    speed_to=target_speed(
                        donor_by_id[pair.donor_id].speed,
                        donor_by_id[pair.donor_id].step_percent,
                    ),
                    distance_m=pair.distance_m,
                    opened_at=closed_at,
                )
                for pair in changes.allocation.pairs
            ],
        )


def _print_changes(ngdu_name: str, changes: NgduChanges) -> None:
    """Сухой прогон: что изменится, по именам скважин."""
    wells, donors = changes.wells, changes.donor_by_id

    def name(well_id: int) -> str:
        well = wells.get(well_id)
        return well.name if well is not None else f"well {well_id}"

    def donor_name(donor_id: int) -> str:
        return name(donors[donor_id].well_id)

    by_pair = {pair.id: pair for pair in changes.open_pairs}
    before = Counter(pair.loss_well_id for pair in changes.open_pairs)
    after = before.copy()
    print(f"\n== {ngdu_name}")  # noqa: T201
    for pair_id, reason in changes.closing:
        pair = by_pair[pair_id]
        after[pair.loss_well_id] -= 1
        print(  # noqa: T201
            f"  закрыть: {name(pair.loss_well_id)} ← {donor_name(pair.donor_id)} "
            f"({reason})",
        )
    for pair in changes.allocation.pairs:
        after[pair.loss_well_id] += 1
        if pair.preempted_pair_id is None:
            print(  # noqa: T201
                f"  новая: {name(pair.loss_well_id)} ← {donor_name(pair.donor_id)}",
            )
            continue
        owner = by_pair[pair.preempted_pair_id].loss_well_id
        after[owner] -= 1
        print(  # noqa: T201
            f"  перехват: донор {donor_name(pair.donor_id)}: {name(owner)} → "
            f"{name(pair.loss_well_id)} ({CLOSE_DONOR_PREEMPTED})",
        )
    losses = sorted(
        set(before) | set(after),
        key=lambda well_id: -(changes.plans.get(well_id, (0.0, None))[0]),
    )
    print("  доноров у потерь (план Qн: было → станет):")  # noqa: T201
    for well_id in losses:
        if before[well_id] or after[well_id]:
            plan = changes.plans.get(well_id, (None, None))[0]
            print(  # noqa: T201
                f"    {name(well_id)} ({plan}): {before[well_id]} → {after[well_id]}",
            )


async def main(*, dry_run: bool = False) -> None:
    async with session_makers["app"]() as session:
        await DonorAssigner(session, dry_run=dry_run).run()


@celery_app.task(name="compensation.assign_donors")
def assign_compensation_donors() -> None:
    run_async(main())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="посчитать и напечатать изменения, ничего не записывая",
    )
    asyncio.run(main(dry_run=parser.parse_args().dry_run))
