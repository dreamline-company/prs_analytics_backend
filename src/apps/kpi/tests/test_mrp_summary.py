"""МРП по методике КМГ через use case: время работы по статусам, ГТМ, ЧРФ."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from apps.kpi.dto.internal.mrp import MrpSummaryDTO
from apps.kpi.dto.queries.mrp import GetMrpSummaryQuery
from apps.kpi.use_cases.get_mrp_summary import (
    GetMrpSummaryUseCase,
    default_end_month,
)
from apps.repairs.services.mrp_inputs import MrpInputsService, fund_months_of

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


class FakeCategories:
    """Периоды категории OIL: (abai_well_id, dbeg, dend)."""

    def __init__(self, rows: list[tuple[int, datetime | None, datetime]]) -> None:
        self.rows = rows

    async def list_intervals_by_code(
        self,
        abai_well_ids: list[int],
        *,
        code: str,
        since: datetime,  # noqa: ARG002
        until: datetime,  # noqa: ARG002
    ) -> list[tuple[int, datetime | None, datetime]]:
        assert code == "OIL"
        return [row for row in self.rows if row[0] in abai_well_ids]


class FakeGeo:
    """Горизонты: ABAI id скважины → названия."""

    async def list_horizon_names_by_abai_well_ids(
        self,
        abai_well_ids: list[int],
        *,
        on_date: date,  # noqa: ARG002
    ) -> dict[int, list[str]]:
        return {i: ["Ю-1", "Ю-2"] for i in abai_well_ids if i == 1002}


class FakeRegimes:
    async def get_current_by_abai_well_ids(
        self,
        abai_well_ids: list[int],
        *,
        on_date: date,  # noqa: ARG002
        grace_days: int,  # noqa: ARG002
    ) -> dict[int, SimpleNamespace]:
        # Плановый режим на конец окна: жидкость, нефть, обводнённость
        return {
            i: SimpleNamespace(liquid=30.0, oil=15.57, water_cut=42.0)
            for i in abai_well_ids
        }


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


# Категория OIL: JLM_0002 числится нефтяной с января (до июля стояла в простое)
CATEGORIES = [
    (1001, _dt("2025-01-01T00:00"), OPEN_END),
    (1002, _dt("2026-01-01T00:00"), OPEN_END),
]


def _use_case(categories: list | None = None) -> GetMrpSummaryUseCase:
    return GetMrpSummaryUseCase(
        ngdu_wells_service=FakeNgduWells(WELLS),  # type: ignore[arg-type]
        mrp_inputs_service=MrpInputsService(
            repair_repository=FakeRepairs(REPAIRS),  # type: ignore[arg-type]
            status_repository=FakeStatuses(STATUSES),  # type: ignore[arg-type]
            expl_repository=FakeExpl(),  # type: ignore[arg-type]
            regime_repository=FakeRegimes(),  # type: ignore[arg-type]
            category_repository=FakeCategories(  # type: ignore[arg-type]
                CATEGORIES if categories is None else categories,
            ),
            geo_repository=FakeGeo(),  # type: ignore[arg-type]
        ),
    )


def _run(categories: list | None = None, **query: object) -> MrpSummaryDTO:
    return asyncio.run(
        _use_case(categories).execute(
            GetMrpSummaryQuery(end_month="2026-09", **query),
            now=NOW,
        ),
    )


def test_window_is_inclusive_of_last_day() -> None:
    summary = _run()

    assert str(summary.window_start) == "2025-10-01"
    assert str(summary.window_end) == "2026-09-30"
    assert summary.months == 12


def test_totals_use_calendar_days_in_fund_minus_repairs() -> None:
    total = _run().total

    # VMB_0001: 365 сут окна − 4 сут ремонтов (ремонты 11, 12, 13) = 361;
    # JLM_0002 в фонде с января по сентябрь: 273 сут − 3 ремонта по 1 сут = 270
    assert total.work_days == pytest.approx(361 + 270, abs=0.3)
    assert total.repairs == 6
    assert total.gtm == 1
    assert total.failures == 5
    assert total.mrp_with_gtm == pytest.approx(total.work_days / 6, abs=0.1)
    assert total.mrp_without_gtm == pytest.approx(total.work_days / 5, abs=0.1)


def test_downtime_days_in_fund_count_as_work_time() -> None:
    by_field = {g.key: g for g in _run().by_oil_field}

    # JLM_0002 до июля стояла в простое, но числилась в фонде (категория OIL и
    # статус «простой» на 1-е число): по методике время работы — календарные
    # сутки фонда минус ремонты, простой его не уменьшает
    assert by_field["JLM"].work_days == pytest.approx(270, abs=0.3)


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


def test_fund_is_limited_by_oil_category() -> None:
    # JLM_0002 стала нефтяной только с августа: её ремонт 21 (10 июля) и время
    # работы июля в расчёт не входят — в фонд июля она не попадает.
    categories = [
        (1001, _dt("2025-01-01T00:00"), OPEN_END),
        (1002, _dt("2026-08-01T00:00"), OPEN_END),
    ]
    by_field = {g.key: g for g in _run(categories).by_oil_field}

    assert by_field["JLM"].repairs == 2
    # август + сентябрь = 61 сут минус два ремонта по 1 сут
    assert by_field["JLM"].work_days == pytest.approx(59, abs=0.5)


def test_well_without_oil_category_is_not_in_fund() -> None:
    summary = _run([(1001, _dt("2025-01-01T00:00"), OPEN_END)])

    assert {g.key for g in summary.by_oil_field} == {"VMB"}
    assert summary.total.repairs == 3


def test_crf_well_has_regime_and_repair_list() -> None:
    well = _run().crf.wells[0]

    assert well.name == "JLM_0002"
    assert (well.regime_liquid, well.regime_oil, well.regime_water_cut) == (
        30.0,
        15.57,
        42.0,
    )
    # горизонты скважины на последний день окна, по названию
    assert well.horizon == "Ю-1, Ю-2"
    # все три ремонта скважины, начатые в окне, по возрастанию даты начала
    assert [r.start.date().isoformat() for r in well.repairs_detail] == [
        "2026-07-10",
        "2026-08-10",
        "2026-09-10",
    ]
    assert all(r.counted and not r.is_gtm for r in well.repairs_detail)


def test_crf_repair_list_marks_gtm_as_not_counted() -> None:
    wells = {w.name: w for w in _run(crf_min_repairs=2).crf.wells}

    # VMB_0001: ремонт 12 («по мероприятию») — ГТМ, в число ремонтов ЧРФ не входит
    flags = {
        r.start.date().isoformat(): (r.is_gtm, r.counted)
        for r in wells["VMB_0001"].repairs_detail
    }
    assert flags["2026-06-01"] == (True, False)
    assert flags["2026-03-10"] == (False, True)
    assert sum(r.counted for r in wells["VMB_0001"].repairs_detail) == wells[
        "VMB_0001"
    ].repairs


def _first(year: int, month: int) -> date:
    return date(year, month, 1)


def test_fund_month_needs_category_and_fund_status_on_the_first() -> None:
    category = [(_dt("2026-01-01T00:00"), OPEN_END)]
    status = [(_dt("2026-02-10T00:00"), OPEN_END)]

    months = fund_months_of(
        [_first(2026, 1), _first(2026, 2), _first(2026, 3)],
        category,
        status,
    )

    # 1 февраля статуса ещё нет — в фонд месяц не попадает
    assert months == {(2026, 3)}


def test_fund_month_ignores_well_without_category_or_status() -> None:
    firsts = [_first(2026, 5)]

    assert fund_months_of(firsts, [], [(_dt("2026-01-01T00:00"), OPEN_END)]) == set()
    assert fund_months_of(firsts, [(_dt("2026-01-01T00:00"), OPEN_END)], []) == set()


def test_fund_period_ends_before_the_first() -> None:
    # период закрыт ровно в полночь 1-го числа — на эту дату уже не действует
    category = [(None, _dt("2026-05-01T00:00"))]
    status = [(_dt("2026-01-01T00:00"), OPEN_END)]

    months = fund_months_of([_first(2026, 4), _first(2026, 5)], category, status)

    assert months == {(2026, 4)}


def test_unknown_category_start_means_since_forever() -> None:
    category = [(None, OPEN_END)]
    status = [(_dt("2000-01-01T00:00"), OPEN_END)]

    assert fund_months_of([_first(2026, 1)], category, status) == {(2026, 1)}
