"""Динамика МРП через use case: окно загрузки и формат ответа."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from apps.kpi.dto.internal.mrp import MrpDynamicsDTO
from apps.kpi.dto.queries.mrp import GetMrpDynamicsQuery
from apps.kpi.use_cases.get_mrp_dynamics import GetMrpDynamicsUseCase
from apps.repairs.services.mrp_inputs import MrpInputsService

NOW = datetime(2026, 10, 14, 10, 0)  # noqa: DTZ001
OPEN_END = datetime(3333, 12, 31)  # noqa: DTZ001


class FakeNgduWells:
    async def list_wells(
        self,
        ngdu_id: int | None = None,  # noqa: ARG002
        *,
        oil_field_id: int | None = None,  # noqa: ARG002
    ) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=1, abai_id=1001, name="VMB_0001")]


class FakeRepairs:
    def __init__(self) -> None:
        self.since: datetime | None = None

    async def list_with_type_by_well_abai_ids(
        self,
        abai_well_ids: list[int],  # noqa: ARG002
        *,
        since: datetime | None = None,
    ) -> list[tuple[SimpleNamespace, str | None]]:
        self.since = since
        repair = SimpleNamespace(
            id=11,
            abai_well_id=1001,
            repair_type_id=3020,
            work_list="Обрыв штанги",
            start_time=datetime(2026, 8, 10),  # noqa: DTZ001
            end_time=datetime(2026, 8, 11),  # noqa: DTZ001
        )
        return [(repair, None)]


class FakeStatuses:
    async def list_intervals_by_abai_well_ids(
        self,
        abai_well_ids: list[int],  # noqa: ARG002
        *,
        since: datetime,  # noqa: ARG002
        until: datetime,  # noqa: ARG002
    ) -> list[tuple]:
        start = datetime(2024, 1, 1)  # noqa: DTZ001
        return [(1001, start, OPEN_END, "WRK", "В работе", None)]


class FakeRegimes:
    async def list_producing_intervals_by_abai_well_ids(
        self,
        abai_well_ids: list[int],
        *,
        since: date,  # noqa: ARG002
        until: date,  # noqa: ARG002
    ) -> list[tuple[int, date, date]]:
        return [(i, date(2024, 1, 1), date(2099, 12, 31)) for i in abai_well_ids]


class FakeExpl:
    async def get_latest_expl_name_by_abai_well_ids(
        self,
        abai_well_ids: list[int],
    ) -> dict[int, str | None]:
        return dict.fromkeys(abai_well_ids)


def _run(**query: object) -> tuple[MrpDynamicsDTO, FakeRepairs]:
    repairs = FakeRepairs()
    use_case = GetMrpDynamicsUseCase(
        ngdu_wells_service=FakeNgduWells(),  # type: ignore[arg-type]
        mrp_inputs_service=MrpInputsService(
            repair_repository=repairs,  # type: ignore[arg-type]
            status_repository=FakeStatuses(),  # type: ignore[arg-type]
            expl_repository=FakeExpl(),  # type: ignore[arg-type]
            regime_repository=FakeRegimes(),  # type: ignore[arg-type]
        ),
    )
    dto = asyncio.run(
        use_case.execute(GetMrpDynamicsQuery(end_month="2026-09", **query), now=NOW),
    )
    return dto, repairs


def test_series_covers_requested_months() -> None:
    dto, _ = _run(points=6)

    assert dto.window_months == 12
    assert dto.points[0].month == "2026-04"
    assert dto.points[-1].month == "2026-09"


def test_load_window_includes_the_first_rolling_period() -> None:
    _, repairs = _run(points=12, window_months=12)

    # ряд из 12 месяцев с 12-месячным окном — 23 месяца: с ноября 2024 года
    # (первая точка — октябрь 2025, её окно — ноябрь 2024 … октябрь 2025)
    assert repairs.since == datetime(2024, 11, 1)  # noqa: DTZ001


def test_repair_month_is_counted() -> None:
    dto, _ = _run(points=3)

    august = next(p for p in dto.points if p.month == "2026-08")
    assert august.month_repairs == 1
    assert august.month_gtm == 0
    assert august.rolling_repairs == 1


def test_default_end_is_previous_month() -> None:
    use_case = GetMrpDynamicsUseCase(
        ngdu_wells_service=FakeNgduWells(),  # type: ignore[arg-type]
        mrp_inputs_service=MrpInputsService(
            repair_repository=FakeRepairs(),  # type: ignore[arg-type]
            status_repository=FakeStatuses(),  # type: ignore[arg-type]
            expl_repository=FakeExpl(),  # type: ignore[arg-type]
            regime_repository=FakeRegimes(),  # type: ignore[arg-type]
        ),
    )
    dto = asyncio.run(use_case.execute(GetMrpDynamicsQuery(points=2), now=NOW))

    assert dto.points[-1].month == "2026-09"
