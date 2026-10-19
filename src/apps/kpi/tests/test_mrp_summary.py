"""МРП по методике КМГ через use case: время работы по статусам, ГТМ, ЧРФ."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.kpi.dto.internal.mrp import MrpSummaryDTO
from apps.kpi.dto.queries.mrp import GetMrpSummaryQuery
from apps.kpi.use_cases.get_mrp_summary import (
    GetMrpSummaryUseCase,
    default_end_month,
)
from apps.repairs.services.mrp_inputs import MrpInputsService

NOW = datetime(2026, 10, 5, 10, 0)  # noqa: DTZ001
OPEN_END = datetime(3333, 12, 31)  # noqa: DTZ001


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _repair(  # noqa: PLR0913, PLR0917
    repair_id: int,
    abai_well_id: int,
    start: str,
    end: str,
    text: str = "Обрыв штанги",
    type_id: int = 3020,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=repair_id,
        abai_well_id=abai_well_id,
        repair_type_id=type_id,
        work_list=text,
        start_time=_dt(start),
        end_time=_dt(end),
    )


class FakeNgduWells:
    def __init__(self, wells: list[SimpleNamespace]) -> None:
        self.wells = wells

    async def list_wells(
        self,
        ngdu_id: int | None = None,  # noqa: ARG002
        *,
        oil_field_id: int | None = None,  # noqa: ARG002
    ) -> list[SimpleNamespace]:
        return self.wells


class FakeRepairs:
    def __init__(self, rows: list[tuple[SimpleNamespace, str | None]]) -> None:
        self.rows = rows

    async def list_with_type_by_well_abai_ids(
        self,
        abai_well_ids: list[int],
        *,
        since: datetime | None = None,  # noqa: ARG002
    ) -> list[tuple[SimpleNamespace, str | None]]:
        return [row for row in self.rows if row[0].abai_well_id in abai_well_ids]


class FakeStatuses:
    """Статусы как в ABAI: местное время, открытый интервал до 3333-12-31."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    async def list_intervals_by_abai_well_ids(
        self,
        abai_well_ids: list[int],
        *,
        since: datetime,  # noqa: ARG002
        until: datetime,  # noqa: ARG002
    ) -> list[tuple]:
        return [row for row in self.rows if row[0] in abai_well_ids]


class FakeExpl:
    async def get_latest_expl_name_by_abai_well_ids(
        self,
        abai_well_ids: list[int],
    ) -> dict[int, str | None]:
        return {i: ("ШГН" if i == 1001 else None) for i in abai_well_ids}


WELLS = [
    SimpleNamespace(id=1, abai_id=1001, name="VMB_0001"),
    SimpleNamespace(id=2, abai_id=1002, name="JLM_0002"),
]
REPAIRS = [
    (_repair(11, 1001, "2026-03-10T00:00", "2026-03-12T00:00"), "Обрыв штанг"),
    (
        _repair(
            12,
            1001,
            "2026-06-01T00:00",
            "2026-06-02T00:00",
            "По мероприятию",
            3012,
        ),
        "По мероприятию",
    ),
    (_repair(13, 1001, "2026-08-05T00:00", "2026-08-06T00:00"), "Обрыв штанг"),
    (_repair(21, 1002, "2026-07-10T00:00", "2026-07-11T00:00"), None),
    (_repair(22, 1002, "2026-08-10T00:00", "2026-08-11T00:00"), None),
    (_repair(23, 1002, "2026-09-10T00:00", "2026-09-11T00:00"), None),
]
STATUSES = [
    # (abai_well_id, dbeg, dend, код, название, причина)
    (1001, _dt("2025-01-01T00:00"), OPEN_END, "WRK", "В работе", None),
    (1002, _dt("2026-07-01T00:00"), OPEN_END, "WRK", "В работе", None),
    # простой не считается временем работы
    (1002, _dt("2026-01-01T00:00"), _dt("2026-06-30T00:00"), "DWN", "Простой", None),
]


def _use_case() -> GetMrpSummaryUseCase:
    return GetMrpSummaryUseCase(
        ngdu_wells_service=FakeNgduWells(WELLS),  # type: ignore[arg-type]
        mrp_inputs_service=MrpInputsService(
            repair_repository=FakeRepairs(REPAIRS),  # type: ignore[arg-type]
            status_repository=FakeStatuses(STATUSES),  # type: ignore[arg-type]
            expl_repository=FakeExpl(),  # type: ignore[arg-type]
        ),
    )


def _run(**query: object) -> MrpSummaryDTO:
    return asyncio.run(
        _use_case().execute(GetMrpSummaryQuery(end_month="2026-09", **query), now=NOW),
    )


def test_window_is_inclusive_of_last_day() -> None:
    summary = _run()

    assert str(summary.window_start) == "2025-10-01"
    assert str(summary.window_end) == "2026-09-30"
    assert summary.months == 12


def test_totals_use_work_status_minus_repairs() -> None:
    total = _run().total

    # A: 365 − 5 сут ремонтов (2+1+1 ... см. ремонты 11, 12, 13) = 361;
    # B: июль–сентябрь 92 − 3 ремонта по 1 сут = 89
    assert total.work_days == pytest.approx(361 + 89, abs=0.3)
    assert total.repairs == 6
    assert total.gtm == 1
    assert total.failures == 5
    assert total.mrp_with_gtm == pytest.approx(total.work_days / 6, abs=0.1)
    assert total.mrp_without_gtm == pytest.approx(total.work_days / 5, abs=0.1)


def test_downtime_status_is_not_work() -> None:
    by_field = {g.key: g for g in _run().by_oil_field}

    # JLM_0002: статус «в работе» только с 1 июля — простой до этого не считается
    assert by_field["JLM"].work_days == pytest.approx(89, abs=0.3)


def test_groups_by_oil_field_and_lift_type() -> None:
    summary = _run()

    assert {g.key for g in summary.by_oil_field} == {"VMB", "JLM"}
    assert {g.key for g in summary.by_lift_type} == {"ШГН", "не указан"}


def test_crf_excludes_gtm_and_uses_threshold() -> None:
    crf = _run().crf

    # VMB_0001: 3 ремонта, один ГТМ → 2 без ГТМ; JLM_0002: 3 отказа
    assert [(w.name, w.repairs) for w in crf.wells] == [("JLM_0002", 3)]
    assert crf.min_repairs == 3
    assert crf.fund_at_end == 2
    assert crf.percent_of_fund == pytest.approx(50.0)
    assert crf.distribution == {2: 1, 3: 1}


def test_crf_threshold_from_query() -> None:
    crf = _run(crf_min_repairs=2).crf

    assert {w.name for w in crf.wells} == {"VMB_0001", "JLM_0002"}


def test_default_window_ends_with_previous_full_month() -> None:
    assert default_end_month(datetime(2026, 10, 5)) == (2026, 9)  # noqa: DTZ001
    assert default_end_month(datetime(2026, 1, 3)) == (2025, 12)  # noqa: DTZ001


def test_note_explains_assumptions() -> None:
    note = _run().note

    assert "ГТМ" in note
    assert "технической причине" in note
