"""Подбор доноров под стоящие скважины — раз в час, по каждому НГДУ пула.

1. Потери: скважины контура (у Кайнармунайгаза — только VMB) в состоянии
   «стоит» или «ремонт» по таблице состояний; величина — факт нефти по ABAI за
   10 суток до остановки, нет факта — план (``services.losses``). Потери
   пишутся в ``compensation_loss`` — их читают эндпоинты.
2. Открытые пары закрываются, если скважина с потерей запустилась, донор
   встал, в аварии или выпал из пула, а пары «на согласовании» сверх лимита
   доноров на потерю — тоже.
3. Непокрытые части потерь добираются донорами пула **той же ГЗУ** —
   крупные приросты раньше; потери, стоящие не меньше ``PREEMPT_AFTER_HOURS``,
   перехватывают доноров «на согласовании» у потерь меньше в
   ``PREEMPT_RATIO`` раза (``allocate``). Перехваченные пары закрываются с
   причиной ``donor_preempted``.

Расчёт отделён от записи: ``--dry-run`` печатает изменения и ничего не пишет
(транзакции только на чтение). Каждый НГДУ — своя транзакция: сбой одного не
мешает остальным.

    python -m apps.compensation.tasks.assign_donors.assign_donors [--dry-run]
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.compensation.constants import (
    CLOSE_DONOR_PREEMPTED,
    PREEMPT_AFTER_HOURS,
    RECOMMENDATION_PENDING,
)
from apps.compensation.dto.internal.repositories.compensation import (
    CreateCompensationLossDTO,
    CreateCompensationRecommendationDTO,
)
from apps.compensation.models.compensation import (
    CompensationDonor,
    CompensationRecommendation,
)
from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationLossRepository,
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
from apps.compensation.services.losses import LossValue, compute_losses
from apps.compensation.services.state import (
    CompensationStateService,
    WellsState,
    apply_losses,
    to_local,
    utc_now,
)
from apps.models_registry import *  # noqa: F403
from apps.org.repositories.org import OrgRepository
from apps.wells.models.well import Well
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


def gzu_group(gzu_abai_id: int | None, fallback: str) -> str:
    """Группа подбора — ГЗУ; без ГЗУ — своя группа, донора там нет."""
    return f"gzu:{gzu_abai_id}" if gzu_abai_id is not None else fallback


@dataclass(slots=True)
class NgduChanges:
    """Что подбор изменит в НГДУ: потери, закрытия, перехваты и новые пары."""

    wells: dict[int, Well]
    donor_by_id: dict[int, CompensationDonor]
    open_pairs: list[CompensationRecommendation]
    state: WellsState
    loss_values: list[LossValue]
    closing: list[tuple[int, str]]  # (пара, причина) — кроме перехватов
    allocation: Allocation


class DonorAssigner:
    def __init__(
        self,
        session: AsyncSession,
        abai: AsyncSession,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> None:
        self.session = session
        self.abai = abai
        self.now = now or utc_now()  # наивный UTC; в местное — to_local
        self.dry_run = dry_run
        self.state_service = CompensationStateService(session)
        self.donor_repository = CompensationDonorRepository(session)
        self.pair_repository = CompensationRecommendationRepository(session)
        self.loss_repository = CompensationLossRepository(session)

    async def run(self) -> None:
        orgs = await OrgRepository(self.session).list_by_abai_ids(
            await self.donor_repository.list_ngdu_ids(),
        )
        # Снимок до цикла: после rollback ORM-объекты протухают.
        ngdus = [
            (org.id, org.abai_id, org.name_ru_short or org.name_ru) for org in orgs
        ]
        await self.session.rollback()
        for ngdu_id, abai_ngdu_id, name in ngdus:
            try:
                if self.dry_run:
                    await self.session.execute(text("SET TRANSACTION READ ONLY"))
                await self._abai_read_only()
                changes = await self._plan(ngdu_id)
                await self.abai.rollback()
                if self.dry_run:
                    _print_changes(name, changes)
                    await self.session.rollback()
                    continue
                await self._apply(changes, abai_ngdu_id)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                await self.abai.rollback()
                logger.exception("Compensation assign ngdu_id=%s failed", ngdu_id)
                continue
            logger.info(
                "Compensation ngdu_id=%s: losses=%s, opened=%s, closed=%s, "
                "preempted=%s",
                ngdu_id,
                len(changes.state.losses),
                len(changes.allocation.pairs),
                len(changes.closing),
                len(changes.allocation.preempted),
            )

    async def _abai_read_only(self) -> None:
        """ABAI недоступен — подбор всё равно идёт: потери по плану, закрытия
        пар не встают (``compute_losses`` сам откатится на план)."""
        try:
            await self.abai.execute(text("SET TRANSACTION READ ONLY"))
        except Exception:
            logger.exception("ABAI unavailable for compensation losses")
            await self.abai.rollback()

    async def _plan(self, ngdu_id: int) -> NgduChanges:
        """Расчёт без записи."""
        local_now = to_local(self.now)
        wells = {
            well.id: well for well in await self.state_service.scope_wells(ngdu_id)
        }
        loss_values = await compute_losses(
            self.session,
            self.abai,
            list(wells.values()),
            now=local_now,
        )
        state = WellsState()
        apply_losses(state, loss_values, on_date=local_now.date())
        state.alarms = await self.state_service.alarms(list(wells.values()))

        donors = await self.donor_repository.list_by_well_ids(list(wells))
        donor_by_id = {donor.id: donor for donor in donors}
        available = {
            donor.well_id
            for donor in donors
            if donor.in_pool
            and donor.well_id not in state.stops
            and donor.well_id not in state.alarms
        }
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
                    gain=pair.gain,
                    loss_group=gzu_group(
                        state.gzu.get(pair.loss_well_id),
                        f"loss:{pair.loss_well_id}",
                    ),
                    donor_group=gzu_group(
                        donor_by_id[pair.donor_id].gzu_abai_id,
                        f"donor:{pair.donor_id}",
                    ),
                )
                for pair in open_pairs
            ],
            stopped_well_ids=set(state.stops),
            available_donor_well_ids=available,
        )
        closed_ids = {pair_id for pair_id, _ in closing}
        kept = [pair for pair in open_pairs if pair.id not in closed_ids]
        busy = {pair.donor_id for pair in kept}

        loss_wells = [wells[well_id] for well_id in state.losses if well_id in wells]
        free = [
            donor
            for donor in donors
            if donor.well_id in available and donor.id not in busy
        ]
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
                gzu_group(donor.gzu_abai_id, f"donor:{donor.id}"),
                donor.gain if gain is None else gain,
                donor.risk,
                points[donor.well_id],
            )

        preempt_since = local_now - timedelta(hours=PREEMPT_AFTER_HOURS)
        allocation = allocate(
            [
                LossInput(
                    well.id,
                    gzu_group(state.gzu.get(well.id), f"loss:{well.id}"),
                    state.losses[well.id][0],
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
                    # пересчитывается каждый день.
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
            state=state,
            loss_values=loss_values,
            closing=closing,
            allocation=allocation,
        )

    async def _apply(self, changes: NgduChanges, abai_ngdu_id: int) -> None:
        """Потери, затем закрытия до вставок: у донора не может быть двух
        открытых пар."""
        local_now = to_local(self.now)
        await self.loss_repository.replace_for_wells(
            list(changes.wells),
            [
                CreateCompensationLossDTO(
                    well_id=value.well_id,
                    abai_ngdu_id=abai_ngdu_id,
                    stop_kind=value.stop_kind,
                    stop_text=value.stop_text,
                    stop_since=value.stop_since,
                    loss=value.loss,
                    loss_source=value.source,
                    gzu_abai_id=value.gzu_abai_id,
                    calculated_at=local_now,
                )
                for value in changes.loss_values
            ],
        )
        for pair_id, reason in changes.closing:
            await self.pair_repository.close(
                pair_id,
                closed_at=local_now,
                reason=reason,
            )
        for pair_id in changes.allocation.preempted:
            closed = await self.pair_repository.close(
                pair_id,
                closed_at=local_now,
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
                    loss=changes.state.losses[pair.loss_well_id][0],
                    gain=donor_by_id[pair.donor_id].gain,
                    speed_from=donor_by_id[pair.donor_id].speed,
                    speed_to=target_speed(
                        donor_by_id[pair.donor_id].speed,
                        donor_by_id[pair.donor_id].step_percent,
                    ),
                    distance_m=pair.distance_m,
                    opened_at=local_now,
                )
                for pair in changes.allocation.pairs
            ],
        )


def _print_changes(ngdu_name: str, changes: NgduChanges) -> None:
    """Сухой прогон: что изменится, по именам скважин."""
    wells, donors, losses = changes.wells, changes.donor_by_id, changes.state.losses

    def name(well_id: int) -> str:
        well = wells.get(well_id)
        return well.name if well is not None else f"well {well_id}"

    def donor_name(donor_id: int) -> str:
        return name(donors[donor_id].well_id)

    by_pair = {pair.id: pair for pair in changes.open_pairs}
    before = Counter(pair.loss_well_id for pair in changes.open_pairs)
    after = before.copy()
    sources = Counter(changes.state.loss_sources.values())
    print(  # noqa: T201
        f"\n== {ngdu_name}: потерь {len(changes.state.stops)}, "
        f"с оценкой {len(losses)} (факт {sources['fact']}, план {sources['plan']}), "
        f"в пуле доноров {sum(d.in_pool for d in donors.values())}",
    )
    for pair_id, reason in changes.closing:
        pair = by_pair[pair_id]
        after[pair.loss_well_id] -= 1
        print(  # noqa: T201
            f"  закрыть: {name(pair.loss_well_id)} ← "
            f"{donor_name(pair.donor_id)} ({reason})",
        )
    for pair in changes.allocation.pairs:
        after[pair.loss_well_id] += 1
        if pair.preempted_pair_id is None:
            print(  # noqa: T201
                f"  новая: {name(pair.loss_well_id)} ← {donor_name(pair.donor_id)} "
                f"+{donors[pair.donor_id].gain:.2f}",
            )
            continue
        owner = by_pair[pair.preempted_pair_id].loss_well_id
        after[owner] -= 1
        print(  # noqa: T201
            f"  перехват: донор {donor_name(pair.donor_id)}: {name(owner)} → "
            f"{name(pair.loss_well_id)} ({CLOSE_DONOR_PREEMPTED})",
        )
    print("  потери (т/сут, источник): доноров было → станет")  # noqa: T201
    for well_id in sorted(losses, key=lambda w: -losses[w][0]):
        print(  # noqa: T201
            f"    {name(well_id)} {losses[well_id][0]:.2f} "
            f"({changes.state.loss_sources.get(well_id, '—')}): "
            f"{before[well_id]} → {after[well_id]}",
        )


async def main(*, dry_run: bool = False) -> None:
    async with session_makers["app"]() as session, session_makers["abai"]() as abai:
        await DonorAssigner(session, abai, dry_run=dry_run).run()


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
