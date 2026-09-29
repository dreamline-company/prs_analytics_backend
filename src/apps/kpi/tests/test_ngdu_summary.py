"""Свёртка сводки НГДУ: пороги и границы без базы."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from apps.kpi.use_cases.get_ngdu_summary import (
    GetNgduSummaryUseCase,
    WellSummaryInput,
    summarize,
)

AS_OF = datetime(2026, 9, 8, 12, 0)  # noqa: DTZ001 — наивный UTC, как в БД


def _row(
    well_id: int,
    fact: float | None,
    plan: float | None,
    *,
    active: bool = False,
    alarm: bool = False,
) -> WellSummaryInput:
    return WellSummaryInput(
        well_id=well_id,
        oil_fact=fact,
        oil_plan=plan,
        is_active=active,
        is_alarm=alarm,
    )


def test_empty_fund() -> None:
    summary = summarize([], as_of=AS_OF)

    assert summary.wells.total == 0
    assert summary.oil_production.value == 0
    assert summary.plan_fulfillment.percent is None
    assert summary.deviations.wells == 0


def test_production_counts_every_fresh_measurement_but_plan_needs_both() -> None:
    rows = [
        _row(1, fact=10.0, plan=10.0, active=True),  # в плане
        _row(2, fact=5.0, plan=None),  # замер есть, плана нет -> только в добыче
        _row(3, fact=None, plan=20.0),  # план есть, замера нет -> нигде
        _row(4, fact=8.0, plan=0.0),  # нулевой план не сравнивается
    ]

    summary = summarize(rows, as_of=AS_OF)

    assert summary.wells.total == 4
    assert summary.wells.active == 1
    assert summary.oil_production.value == 23.0
    assert summary.oil_production.wells_measured == 3
    assert summary.plan_fulfillment.wells == 1
    assert summary.plan_fulfillment.fact == 10.0
    assert summary.plan_fulfillment.plan == 10.0
    assert summary.plan_fulfillment.percent == 100.0


def test_losses_by_plan_threshold() -> None:
    rows = [
        _row(1, fact=9.0, plan=10.0),  # -10% ровно на пороге -> не недобор
        _row(2, fact=8.9, plan=10.0),  # ниже порога -> потери 1.1
        _row(3, fact=5.0, plan=20.0),  # потери 15
        _row(4, fact=30.0, plan=20.0),  # перевыполнение не считается
    ]

    summary = summarize(rows, as_of=AS_OF)

    assert summary.deviations.losses == 16.1
    assert summary.deviations.threshold_percent == 10.0
    assert summary.plan_fulfillment.percent == round((9 + 8.9 + 5 + 30) / 60 * 100, 1)


def test_deviation_wells_are_alarm_wells() -> None:
    rows = [
        _row(1, fact=None, plan=None, alarm=True),  # без замера, но alarm
        _row(2, fact=5.0, plan=20.0),  # недобор, но не alarm
        _row(3, fact=10.0, plan=10.0, alarm=True),
    ]

    summary = summarize(rows, as_of=AS_OF)

    assert summary.deviations.wells == 2
    assert summary.deviations.losses == 15.0


KMG_ORG_ID = 5
KMG_WELLS = [
    SimpleNamespace(id=1, name="VMB_0001"),
    SimpleNamespace(id=2, name="UAZ_0002"),
]
ZHMG_WELL = SimpleNamespace(id=3, name="UZK_0003")


class _NgduWells:
    async def list_wells(self, ngdu_id: int | None) -> list[SimpleNamespace]:
        return KMG_WELLS if ngdu_id == KMG_ORG_ID else [*KMG_WELLS, ZHMG_WELL]


class _Orgs:
    async def list_by_abai_ids(self, _abai_ids: list[int]) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=KMG_ORG_ID, abai_id=12)]


def test_kainar_wells_only_from_vmb_in_any_scope() -> None:
    use_case = GetNgduSummaryUseCase(
        ngdu_wells_service=_NgduWells(),
        org_repository=_Orgs(),
        telemetry_repository=None,
        tech_regime_repository=None,
        sdmo_fc_data_repository=None,
        well_incident_status_service=None,
    )

    all_ngdu = asyncio.run(use_case._drop_kmg_outside_vmb([*KMG_WELLS, ZHMG_WELL]))  # noqa: SLF001
    kmg_only = asyncio.run(use_case._drop_kmg_outside_vmb(KMG_WELLS))  # noqa: SLF001

    assert [well.name for well in all_ngdu] == ["VMB_0001", "UZK_0003"]
    assert [well.name for well in kmg_only] == ["VMB_0001"]
