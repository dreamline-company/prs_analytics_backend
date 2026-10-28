"""Хронология событий скважины: случаи «сигнал → поломка → начало ремонта»."""

# Времена в тестах — местные наивные, как в ABAI и ремонтах.
# ruff: noqa: DTZ001

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.wells.dto.queries.well import GetWellTimelineQuery
from apps.wells.services.well_timeline.rules import (
    CASE_FAILURE,
    CASE_SIGNAL,
    REPAIR_SOURCE_ABAI,
    REPAIR_SOURCE_REPAIR,
    IncidentInput,
    RepairInput,
    StatusInput,
    TimelineCase,
    build_cases,
)
from apps.wells.use_cases.get_well_card import WellNotFoundError
from apps.wells.use_cases.get_well_timeline import GetWellTimelineUseCase

NOW = datetime(2026, 10, 28, 12, 0)
SINCE = NOW - timedelta(days=365)
OPEN_DEND = datetime(3333, 12, 31)


def _wrk(since: datetime, until: datetime | None = None) -> StatusInput:
    return StatusInput(since, until, "WRK", "В работе", None)


def _dwn(reason: str, since: datetime, until: datetime | None = None) -> StatusInput:
    return StatusInput(since, until, "DWN", "В простое", reason)


def _repair(  # noqa: PLR0913
    repair_id: int,
    start: datetime,
    end: datetime | None = None,
    *,
    type_name: str | None = "Ревизия и смена насоса",
    work_list: str | None = None,
    abai_type: int | None = 3,
) -> RepairInput:
    return RepairInput(repair_id, start, end, None, type_name, work_list, abai_type)


def _incident(  # noqa: PLR0913
    incident_id: int,
    opened_at: datetime,
    *,
    detector: str = "R9",
    status: str = "normalized",
    verdict: str | None = None,
    repair_ids: frozenset[int] = frozenset(),
) -> IncidentInput:
    return IncidentInput(
        incident_id=incident_id,
        detector_code=detector,
        title=detector,
        reason_code="signal",
        level="alarm",
        status=status,
        opened_at=opened_at,
        verdict=verdict,
        repair_ids=repair_ids,
    )


def _build(
    statuses: list[StatusInput] | None = None,
    repairs: list[RepairInput] | None = None,
    incidents: list[IncidentInput] | None = None,
) -> list[TimelineCase]:
    return build_cases(
        statuses=statuses or [],
        repairs=repairs or [],
        incidents=incidents or [],
        since=SINCE,
        now=NOW,
    )


def _signal_ids(case: TimelineCase) -> list[int]:
    return [s.incident_id for s in case.signals]


# Поломка 18.05 14:02 «Нет подачи», ПРС 21.05 09:00–23.05 18:30.
BREAK = datetime(2026, 5, 18, 14, 2)
START = datetime(2026, 5, 21, 9, 0)
END = datetime(2026, 5, 23, 18, 30)
PUMP_FAILURE = [
    _wrk(datetime(2026, 5, 1), BREAK),
    _dwn("Нет подачи", BREAK, START),
    _dwn("ПРС", START, END),
    _wrk(END),
]


def test_full_case_signal_breakdown_repair() -> None:
    cases = _build(
        PUMP_FAILURE,
        [_repair(63405, START, END)],
        [_incident(1, datetime(2026, 5, 15))],
    )

    assert len(cases) == 1
    case = cases[0]
    assert case.kind == CASE_FAILURE
    assert not case.is_open
    assert _signal_ids(case) == [1]
    assert case.breakdown is not None
    assert (case.breakdown.at, case.breakdown.title) == (BREAK, "Нет подачи")
    assert case.repair is not None
    assert case.repair.at == START
    assert case.repair.end_at == END
    assert case.repair.source == REPAIR_SOURCE_REPAIR
    assert case.repair.repair_id == 63405
    assert case.repair.kind == "ПРС"
    assert case.repair.title == "Ревизия и смена насоса"


def test_signal_after_breakdown_before_repair_is_attached() -> None:
    # Сигнал — факт, а не прогноз: может прийти и после поломки.
    late = _incident(2, BREAK + timedelta(hours=4), detector="R2")

    (case,) = _build(PUMP_FAILURE, [_repair(63405, START, END)], [late])

    assert _signal_ids(case) == [2]
    assert case.breakdown is not None
    assert case.signals[0].opened_at > case.breakdown.at


def test_breakdown_is_first_failure_status_in_chain() -> None:
    power_off = datetime(2026, 5, 17, 8, 0)
    waiting = datetime(2026, 5, 19, 10, 0)
    statuses = [
        _wrk(datetime(2026, 5, 1), power_off),
        _dwn("Отключение электроэнергии", power_off, BREAK),
        _dwn("Нет подачи", BREAK, waiting),
        _dwn("ОПРС", waiting, START),
        _dwn("ПРС", START, END),
        _wrk(END),
    ]

    (case,) = _build(statuses, [_repair(1, START, END)])

    assert case.breakdown is not None
    assert (case.breakdown.at, case.breakdown.title) == (BREAK, "Нет подачи")


def test_repair_straight_from_work_has_no_breakdown() -> None:
    statuses = [
        _wrk(datetime(2026, 5, 1), START),
        _dwn("ПРС", START, END),
        _wrk(END),
    ]

    (case,) = _build(statuses, [_repair(1, START, END)])

    # Прочерк, а не дата ремонта.
    assert case.breakdown is None
    assert case.repair is not None
    assert case.repair.at == START


def test_gtm_repair_is_not_a_case() -> None:
    statuses = [
        _wrk(datetime(2026, 5, 1), START),
        _dwn("ГТМ", START, END),
        _wrk(END),
    ]
    gtm_name = "Подготовка и проведение гидроразрыва пласта"
    gtm = _repair(1, START, END, type_name=gtm_name)

    assert _build(statuses, [gtm], [_incident(1, START - timedelta(days=2))]) == []


def test_back_to_back_repairs_split_chain_and_signal_goes_to_earlier() -> None:
    first_break = datetime(2026, 5, 30, 8, 0)
    a_start, a_end = datetime(2026, 6, 1, 8, 0), datetime(2026, 6, 3, 8, 0)
    second_break = datetime(2026, 6, 4, 8, 0)
    b_start, b_end = datetime(2026, 6, 6, 8, 0), datetime(2026, 6, 8, 8, 0)
    statuses = [
        _wrk(datetime(2026, 5, 1), first_break),
        _dwn("Нет подачи", first_break, a_start),
        _dwn("ПРС", a_start, second_break),
        _dwn("Заклин", second_break, b_start),
        _dwn("ПРС", b_start, b_end),
        _wrk(b_end),
    ]
    repairs = [_repair(1, a_start, a_end), _repair(2, b_start, b_end)]
    # В окне обоих ремонтов — достаётся более раннему.
    signal = _incident(1, datetime(2026, 5, 29))

    later, earlier = _build(statuses, repairs, [signal])

    assert earlier.repair is not None
    assert earlier.repair.repair_id == 1
    assert earlier.breakdown is not None
    assert earlier.breakdown.at == first_break
    assert _signal_ids(earlier) == [1]
    assert later.repair is not None
    assert later.repair.repair_id == 2
    # Поломка второго — после конца первого ремонта, не «Нет подачи» 30.05.
    assert later.breakdown is not None
    assert later.breakdown.at == second_break
    assert _signal_ids(later) == []


def test_false_alarm_is_not_a_signal() -> None:
    false = _incident(
        1,
        BREAK - timedelta(days=1),
        detector="R2",
        verdict="false_alarm",
    )
    active_false = _incident(
        2,
        NOW - timedelta(hours=3),
        detector="R2",
        status="active",
        verdict="false_alarm",
    )

    cases = _build(PUMP_FAILURE, [_repair(1, START, END)], [false, active_false])

    assert [c.kind for c in cases] == [CASE_FAILURE]
    assert _signal_ids(cases[0]) == []


def test_incident_inside_previous_repair_is_not_a_signal() -> None:
    a_start, a_end = datetime(2026, 5, 10, 8, 0), datetime(2026, 5, 12, 8, 0)
    statuses = [
        _wrk(datetime(2026, 5, 1), a_start),
        _dwn("ПРС", a_start, a_end),
        _wrk(a_end, BREAK),
        *PUMP_FAILURE[1:],
    ]
    repairs = [_repair(1, a_start, a_end), _repair(2, START, END)]
    # Тревога во время первого ремонта: в окне второго, но это артефакт работ.
    during = _incident(1, datetime(2026, 5, 11, 10, 0), status="active")

    cases = _build(statuses, repairs, [during])

    assert [c.kind for c in cases] == [CASE_FAILURE, CASE_FAILURE]
    assert all(c.signals == [] for c in cases)


def test_confirmed_r2_with_repair_evidence_attached_outside_window() -> None:
    old = _incident(
        1,
        BREAK - timedelta(days=20),
        detector="R2",
        verdict="failure_confirmed",
        repair_ids=frozenset({63405}),
    )

    (case,) = _build(PUMP_FAILURE, [_repair(63405, START, END)], [old])

    assert _signal_ids(case) == [1]


def test_active_incident_without_failure_is_open_signal_case() -> None:
    statuses = [_wrk(datetime(2026, 9, 1))]
    r10 = _incident(1, NOW - timedelta(days=2), detector="R10", status="active")
    r9 = _incident(2, NOW - timedelta(days=1), detector="R9", status="active")
    closed = _incident(3, NOW - timedelta(days=30), detector="R2")

    (case,) = _build(statuses, [], [r9, closed, r10])

    assert case.kind == CASE_SIGNAL
    assert case.is_open
    assert case.breakdown is None
    assert case.repair is None
    # Закрытый эпизод без отказа в хронологию не попадает.
    assert _signal_ids(case) == [1, 2]


def test_open_failure_without_repair() -> None:
    stop = datetime(2026, 10, 19, 16, 19)
    statuses = [_wrk(datetime(2026, 9, 1), stop), _dwn("Нет подачи", stop)]
    signal = _incident(1, datetime(2026, 10, 19, 15, 45), detector="R2")

    (case,) = _build(statuses, [], [signal])

    assert case.kind == CASE_FAILURE
    assert case.is_open
    assert case.breakdown is not None
    assert case.breakdown.at == stop
    assert case.repair is None
    assert _signal_ids(case) == [1]


@pytest.mark.parametrize(
    ("reason", "is_breakdown"),
    [
        ("TP 6-1 Ревизия, смена, опрессовка и устранение негерметичности НКТ", True),
        ("ТР 4-1 Ревизия и смена насоса", True),
        ("TP 9-2 Подготовка к проведению ГИС (перфорация, запись ГИС);", False),
        ("Высокая обводненность", False),
    ],
)
def test_work_type_reason_is_breakdown_unless_gtm(
    reason: str,
    *,
    is_breakdown: bool,
) -> None:
    statuses = [
        _wrk(datetime(2026, 5, 1), BREAK),
        _dwn(reason, BREAK, START),
        _dwn("ПРС", START, END),
        _wrk(END),
    ]

    (case,) = _build(statuses, [_repair(1, START, END)])

    assert (case.breakdown is not None) is is_breakdown


def test_open_failure_older_than_period_is_hidden() -> None:
    stop = SINCE - timedelta(days=30)
    statuses = [
        _wrk(stop - timedelta(days=100), stop),
        _dwn("Авария подземного оборудования", stop),
    ]

    assert _build(statuses) == []


def test_repair_status_without_repair_row_gives_abai_repair_node() -> None:
    stop = datetime(2026, 10, 25, 10, 0)
    prs = datetime(2026, 10, 27, 9, 0)
    statuses = [
        _wrk(datetime(2026, 9, 1), stop),
        _dwn("Нет подачи", stop, prs),
        _dwn("ПРС", prs),
    ]

    (case,) = _build(statuses)

    assert case.is_open
    assert case.breakdown is not None
    assert case.breakdown.at == stop
    assert case.repair is not None
    assert case.repair.source == REPAIR_SOURCE_ABAI
    assert case.repair.repair_id is None
    assert case.repair.kind == "ПРС"
    assert case.repair.at == prs


def test_closed_failure_without_repair() -> None:
    stop, start = datetime(2026, 10, 10, 6, 0), datetime(2026, 10, 11, 9, 0)
    statuses = [
        _wrk(datetime(2026, 9, 1), stop),
        _dwn("Нет подачи", stop, start),
        _wrk(start),
    ]

    (case,) = _build(statuses)

    assert case.kind == CASE_FAILURE
    assert not case.is_open
    assert case.breakdown is not None
    assert case.breakdown.at == stop
    assert case.repair is None


def test_open_cases_first_then_newest() -> None:
    old_start = datetime(2026, 3, 1, 8, 0)
    old_end = datetime(2026, 3, 3, 8, 0)
    stop = datetime(2026, 10, 20, 8, 0)
    statuses = [
        _wrk(datetime(2026, 2, 1), old_start),
        _dwn("ПРС", old_start, old_end),
        _wrk(old_end, BREAK),
        *PUMP_FAILURE[1:3],
        _wrk(END, stop),
        _dwn("Нет подачи", stop),
    ]
    repairs = [_repair(1, old_start, old_end), _repair(2, START, END)]

    cases = _build(statuses, repairs)

    assert [(c.is_open, c.anchor) for c in cases] == [
        (True, stop),
        (False, START),
        (False, old_start),
    ]


# --- Замечания ревью 28.10.2026 ---

FIRST_BREAK = datetime(2026, 9, 25, 8, 0)
REPAIR_START = datetime(2026, 10, 1, 8, 0)
REPAIR_END = datetime(2026, 10, 3, 8, 0)


def test_chain_tail_after_repair_is_new_open_failure() -> None:
    # Ремонт не помог: после него скважина так и не заработала.
    statuses = [
        _wrk(datetime(2026, 9, 1), FIRST_BREAK),
        _dwn("Нет подачи", FIRST_BREAK, REPAIR_START),
        _dwn("ПРС", REPAIR_START, REPAIR_END),
        _dwn("Нет подачи", REPAIR_END),
    ]
    r2 = _incident(1, datetime(2026, 10, 4, 10, 0), detector="R2", status="active")

    current, done = _build(statuses, [_repair(1, REPAIR_START, REPAIR_END)], [r2])

    assert current.is_open
    assert current.repair is None
    assert current.breakdown is not None
    assert current.breakdown.at == REPAIR_END
    assert _signal_ids(current) == [1]
    assert not done.is_open
    assert done.breakdown is not None
    assert done.breakdown.at == FIRST_BREAK


def test_chain_tail_with_repair_status_without_row() -> None:
    second = datetime(2026, 10, 26, 9, 0)
    statuses = [
        _wrk(datetime(2026, 9, 1), FIRST_BREAK),
        _dwn("Нет подачи", FIRST_BREAK, REPAIR_START),
        _dwn("ПРС", REPAIR_START, REPAIR_END),
        _dwn("Нет подачи", REPAIR_END, second),
        _dwn("ПРС", second),
    ]

    current, _done = _build(statuses, [_repair(1, REPAIR_START, REPAIR_END)])

    assert current.is_open
    assert current.repair is not None
    assert (current.repair.source, current.repair.at) == (REPAIR_SOURCE_ABAI, second)
    assert current.breakdown is not None
    assert current.breakdown.at == REPAIR_END


def test_repair_left_open_in_abai_ends_when_well_works() -> None:
    hanging = datetime(2025, 12, 1, 8, 0)
    works = datetime(2025, 12, 5, 8, 0)
    statuses = [
        _wrk(datetime(2025, 11, 1), hanging),
        _dwn("КРС", hanging, works),
        _wrk(works, BREAK),
        *PUMP_FAILURE[1:],
    ]
    repairs = [_repair(1, hanging, None, abai_type=1), _repair(2, START, END)]
    before = _incident(1, datetime(2026, 5, 15))
    now_active = _incident(2, NOW - timedelta(days=1), detector="R10", status="active")

    cases = _build(statuses, repairs, [before, now_active])

    signal, pump, old = cases
    assert signal.kind == CASE_SIGNAL
    assert _signal_ids(signal) == [2]
    # Забытый в ABAI ремонт кончился, когда скважина заработала.
    assert old.repair is not None
    assert old.repair.repair_id == 1
    assert not old.is_open
    assert old.repair.end_at == works
    # Поломка и сигнал следующего ремонта не заглушены.
    assert pump.breakdown is not None
    assert pump.breakdown.at == BREAK
    assert _signal_ids(pump) == [1]


def test_stale_open_status_is_closed_by_next_one() -> None:
    stop = datetime(2026, 10, 20, 8, 0)
    works = datetime(2026, 10, 28, 11, 30)
    # Старый интервал ещё открыт (перечитка ABAI раз в час), WRK уже пришёл.
    statuses = [_wrk(datetime(2026, 9, 1), stop), _dwn("Нет подачи", stop), _wrk(works)]
    fresh = _incident(1, datetime(2026, 10, 28, 11, 45), detector="R2", status="active")

    signal, failure = _build(statuses, [], [fresh])

    assert not failure.is_open
    assert failure.breakdown is not None
    assert failure.breakdown.at == stop
    assert _signal_ids(failure) == []
    assert signal.kind == CASE_SIGNAL
    assert _signal_ids(signal) == [1]


def test_incident_inside_repair_status_without_row_is_not_a_signal() -> None:
    stop = datetime(2026, 10, 25, 10, 0)
    prs = datetime(2026, 10, 26, 9, 0)
    statuses = [
        _wrk(datetime(2026, 9, 1), stop),
        _dwn("Нет подачи", stop, prs),
        _dwn("ПРС", prs),
    ]
    during = _incident(1, datetime(2026, 10, 27), detector="R2", status="active")

    cases = _build(statuses, [], [during])

    assert [c.kind for c in cases] == [CASE_FAILURE]
    assert cases[0].signals == []


def test_breakdown_without_repair_does_not_depend_on_history_depth() -> None:
    old = StatusInput(
        datetime(2024, 1, 1),
        datetime(2024, 6, 1),
        "DWN",
        "В простое",
        "Нет подачи",
    )
    idle = StatusInput(
        datetime(2024, 6, 1),
        datetime(2026, 8, 1),
        "CNS",
        "Консервация",
        "Прочие причины",
    )
    accident = _dwn("Авария подземного оборудования", datetime(2026, 8, 1))

    full = _build([_wrk(datetime(2023, 1, 1), old.since), old, idle, accident])
    cut = _build([idle, accident])

    assert [c.breakdown for c in full] == [c.breakdown for c in cut]
    assert full[0].breakdown is not None
    assert full[0].breakdown.at == datetime(2026, 8, 1)


def test_hidden_old_case_does_not_take_active_signal() -> None:
    stop = SINCE - timedelta(days=120)
    statuses = [_wrk(stop - timedelta(days=30), stop), _dwn("Нет подачи", stop)]
    r10 = _incident(1, NOW - timedelta(days=3), detector="R10", status="active")

    (case,) = _build(statuses, [], [r10])

    assert case.kind == CASE_SIGNAL
    assert _signal_ids(case) == [1]


# --- Сценарий: загрузка данных и 404 ---


class _Wells:
    def __init__(self, well: SimpleNamespace | None) -> None:
        self.well = well

    async def get_by_id(self, **_kwargs: object) -> SimpleNamespace | None:
        return self.well


class _Statuses:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    async def list_intervals_by_abai_well_ids(
        self,
        _abai_well_ids: list[int],
        **_kwargs: object,
    ) -> list[tuple]:
        return self.rows


class _Repairs:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    async def list_with_type_by_well_abai_ids(
        self,
        _abai_well_ids: list[int],
        **_kwargs: object,
    ) -> list[tuple]:
        return self.rows


class _Incidents:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    async def list_by_well_id(self, **_kwargs: object) -> list[SimpleNamespace]:
        return self.rows


class _Detectors:
    async def get_list(self, _spec: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(code="R2", name_ru="Обрыв штанг")]


class _Verifications:
    def __init__(self, rows: dict[int, SimpleNamespace]) -> None:
        self.rows = rows

    async def get_map_by_incident_ids(
        self,
        _incident_ids: list[int],
    ) -> dict[int, SimpleNamespace]:
        return self.rows


def _use_case(
    *,
    well: SimpleNamespace | None = None,
    statuses: list[tuple] | None = None,
    repairs: list[tuple] | None = None,
    incidents: list[SimpleNamespace] | None = None,
    verifications: dict[int, SimpleNamespace] | None = None,
) -> GetWellTimelineUseCase:
    return GetWellTimelineUseCase(
        well_repository=_Wells(well),
        well_status_repository=_Statuses(statuses or []),
        repair_repository=_Repairs(repairs or []),
        incident_repository=_Incidents(incidents or []),
        detector_repository=_Detectors(),
        verification_repository=_Verifications(verifications or {}),
    )


def test_use_case_unknown_well_is_404() -> None:
    with pytest.raises(WellNotFoundError):
        asyncio.run(_use_case().execute(GetWellTimelineQuery(well_id=1), now=NOW))


def test_use_case_well_without_events_has_no_cases() -> None:
    well = SimpleNamespace(id=7, abai_id=70)

    result = asyncio.run(
        _use_case(well=well).execute(GetWellTimelineQuery(well_id=7), now=NOW),
    )

    assert result.well_id == 7
    assert result.cases == []


def test_use_case_maps_rows_and_evidence() -> None:
    well = SimpleNamespace(id=7, abai_id=70)
    stop = datetime(2026, 10, 25, 10, 0)
    start = datetime(2026, 10, 27, 9, 0)
    statuses = [
        (70, datetime(2026, 9, 1), stop, "WRK", "В работе", None),
        (70, stop, start, "DWN", "В простое", "Нет подачи"),
        # Идущий статус в ABAI закрыт датой 3333-12-31.
        (70, start, OPEN_DEND, "DWN", "В простое", "ПРС"),
    ]
    repair = SimpleNamespace(
        id=5,
        start_time=start,
        end_time=None,
        repair_type_id=None,
        work_list=None,
        abai_repair_type_id=3,
    )
    incident = SimpleNamespace(
        id=11,
        detector_code="R2",
        reason_code="rod_break",
        level="alarm",
        status="normalized",
        # Вне окна 14 суток — привязка только по доказательству проверки.
        opened_at=stop - timedelta(days=20),
    )
    verification = SimpleNamespace(
        verdict="failure_confirmed",
        evidence={"repair": [{"repair_id": 5, "start": "2026-10-27T09:00:00"}]},
    )

    result = asyncio.run(
        _use_case(
            well=well,
            statuses=statuses,
            repairs=[(repair, "Ревизия и смена насоса")],
            incidents=[incident],
            verifications={11: verification},
        ).execute(GetWellTimelineQuery(well_id=7), now=NOW),
    )

    (case,) = result.cases
    assert case.kind == "failure"
    assert case.is_open
    assert case.breakdown is not None
    assert (case.breakdown.at, case.breakdown.title) == (stop, "Нет подачи")
    assert case.repair is not None
    assert (case.repair.source, case.repair.repair_id, case.repair.kind) == (
        "repair",
        5,
        "ПРС",
    )
    assert case.signal is not None
    assert (case.signal.incident_id, case.signal.title, case.signal.verdict) == (
        11,
        "Обрыв штанг",
        "failure_confirmed",
    )
    assert [s.incident_id for s in case.signals] == [11]
