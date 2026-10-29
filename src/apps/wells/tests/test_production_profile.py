"""Профиль добычи за период: точки, дебит по медиане трёх, факт с простоями."""

# Времена в тестах — местные наивные, как в данных.
# ruff: noqa: DTZ001

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from apps.wells.dto.internal.well_production_profile import ProfilePeriod
from apps.wells.services.production_profile import (
    SOURCE_ABAI,
    SOURCE_MIXED,
    SOURCE_SDMO,
    Bucket,
    Measurement,
    PlanRegime,
    WellProductionProfileService,
    WorkShare,
    abai_work_share,
    build_profile,
    hour_buckets,
    hourly_work_shares,
    period_buckets,
    plan_on,
    point_work_shares,
    rate_before,
)

NOW = datetime(2026, 10, 20, 14, 37)
FAR = datetime(3333, 12, 31)
OCTOBER = PlanRegime(date(2026, 10, 1), date(2026, 10, 31), 4.0)
SEPTEMBER = PlanRegime(date(2026, 9, 1), date(2026, 9, 30), 2.0)


def _m(moment: datetime, oil: float, liquid: float = 20.0) -> Measurement:
    return Measurement(moment, oil, liquid)


def _shares(count: int, share: float = 1.0) -> list[WorkShare | None]:
    return [WorkShare(share, SOURCE_SDMO)] * count


def test_day_period_is_24_hours_ending_now() -> None:
    step, buckets = period_buckets(ProfilePeriod.DAY, NOW)
    assert step == "hour"
    assert len(buckets) == 24
    assert buckets[0] == Bucket(datetime(2026, 10, 19, 15), datetime(2026, 10, 19, 16))
    assert buckets[-1] == Bucket(datetime(2026, 10, 20, 14), NOW)


def test_three_days_week_and_month_periods() -> None:
    _step, three_days = period_buckets(ProfilePeriod.THREE_DAYS, NOW)
    assert len(three_days) == 72
    assert three_days[0].start == datetime(2026, 10, 17, 15)

    step, week = period_buckets(ProfilePeriod.WEEK, NOW)
    assert step == "day"
    assert [bucket.start.day for bucket in week] == [14, 15, 16, 17, 18, 19, 20]
    assert week[-1].end == NOW

    _step, month = period_buckets(ProfilePeriod.MONTH, NOW)
    assert len(month) == 20
    assert month[0].start == datetime(2026, 10, 1)


def test_period_on_exact_boundary_keeps_full_points() -> None:
    _step, day = period_buckets(ProfilePeriod.DAY, datetime(2026, 10, 20, 14, 0))
    assert len(day) == 24
    assert day[-1] == Bucket(datetime(2026, 10, 20, 13), datetime(2026, 10, 20, 14))

    # Полночь 1-го числа — ещё прошлый месяц целиком.
    _step, month = period_buckets(ProfilePeriod.MONTH, datetime(2026, 11, 1))
    assert len(month) == 31
    assert month[0].start == datetime(2026, 10, 1)


def test_rate_is_median_of_last_three_before_moment() -> None:
    working = [
        _m(datetime(2026, 10, 10, 6), 5.0),
        _m(datetime(2026, 10, 12, 6), 15.7),  # выброс
        _m(datetime(2026, 10, 14, 6), 5.4),
        _m(datetime(2026, 10, 16, 6), 5.2),
    ]
    assert rate_before(working, datetime(2026, 10, 16, 6)) == 5.4
    assert rate_before(working, datetime(2026, 10, 17)) == 5.4
    assert rate_before(working, datetime(2026, 10, 11)) == 5.0
    assert rate_before(working, datetime(2026, 10, 1)) is None


def test_fact_counts_downtime_by_hour() -> None:
    # Дебит 8, скважина 6 часов из 24 стояла — факт 6.
    now = datetime(2026, 10, 20, 15, 0)
    measurements = [
        _m(datetime(2026, 10, 19, 10), 8.0),
        _m(datetime(2026, 10, 20, 6), 8.0),
    ]
    shares = _shares(18) + [WorkShare(0.0, SOURCE_SDMO)] * 6
    profile = build_profile(
        ProfilePeriod.DAY,
        now=now,
        measurements=measurements,
        work_shares=shares,
        regimes=[OCTOBER],
    )
    assert profile.has_measurements
    assert profile.measurements_count == 1
    assert profile.fact_oil == 6.0
    assert profile.points[0].fact_oil == 8.0
    assert profile.points[-1].fact_oil == 0.0
    assert profile.oil_rate_working == 8.0
    assert profile.plan_oil == 4.0
    assert profile.work_share == 0.75
    assert profile.work_share_source == SOURCE_SDMO


def test_fact_over_period_is_time_weighted() -> None:
    # 20 суток по 8, затем дебит 5 — факт ближе к 8, а не медиана замеров.
    now = datetime(2026, 10, 30, 12, 0)
    measurements = [
        _m(datetime(2026, 10, day, 6), 8.0 if day <= 20 else 5.0)
        for day in range(1, 30)
    ]
    _step, buckets = period_buckets(ProfilePeriod.MONTH, now)
    profile = build_profile(
        ProfilePeriod.MONTH,
        now=now,
        measurements=measurements,
        work_shares=_shares(len(buckets)),
        regimes=[OCTOBER],
    )
    assert profile.measurements_count == 29
    assert 6.5 < profile.fact_oil < 7.5
    assert profile.points[0].fact_oil == 8.0
    assert profile.points[-1].fact_oil == 5.0


def test_no_measurements_in_period_means_no_fact() -> None:
    # Замер был позавчера: за сутки «Замера нет», старый не подставляется.
    measurements = [_m(datetime(2026, 10, 18, 6), 8.0)]
    profile = build_profile(
        ProfilePeriod.DAY,
        now=NOW,
        measurements=measurements,
        work_shares=_shares(24),
        regimes=[OCTOBER],
    )
    assert not profile.has_measurements
    assert profile.fact_oil is None
    assert profile.oil_rate_working is None
    assert all(point.fact_oil is None for point in profile.points)
    assert profile.plan_oil == 4.0
    assert profile.measurements == []
    # Дебит по старым замерам тоже не отдаётся — «Замера нет» без подмены.
    assert all(point.oil_rate is None for point in profile.points)


def test_stopped_whole_period_is_zero_without_measurements() -> None:
    profile = build_profile(
        ProfilePeriod.DAY,
        now=NOW,
        measurements=[],
        work_shares=[WorkShare(0.0, SOURCE_ABAI)] * 24,
        regimes=[],
    )
    assert profile.fact_oil == 0.0
    assert not profile.has_measurements
    assert profile.plan_oil is None


def test_stopped_measurements_are_not_in_work() -> None:
    measurements = [
        _m(datetime(2026, 10, 20, 6), 0.0, liquid=0.0),  # стояла — жидкости нет
        Measurement(datetime(2026, 10, 20, 7), None, 20.0),  # нефть не определена
    ]
    profile = build_profile(
        ProfilePeriod.DAY,
        now=NOW,
        measurements=measurements,
        work_shares=_shares(24),
        regimes=[OCTOBER],
    )
    assert not profile.has_measurements
    assert profile.fact_oil is None
    assert len(profile.measurements) == 2


def test_partial_last_point_weighs_less() -> None:
    # Последний час — 30 минут простоя целиком; вес вдвое меньше полного часа.
    now = datetime(2026, 10, 20, 14, 30)
    measurements = [
        _m(datetime(2026, 10, 19, 10), 10.0),
        _m(datetime(2026, 10, 20, 6), 10.0),
    ]
    shares = [*_shares(23), WorkShare(0.0, SOURCE_SDMO)]
    profile = build_profile(
        ProfilePeriod.DAY,
        now=now,
        measurements=measurements,
        work_shares=shares,
        regimes=[OCTOBER],
    )
    assert profile.fact_oil == round(10.0 * 23 / 23.5, 2)


def test_plan_follows_regime_of_each_point() -> None:
    now = datetime(2026, 10, 4, 12, 0)
    measurements = [_m(datetime(2026, 9, 28, 6), 3.0)]
    profile = build_profile(
        ProfilePeriod.WEEK,
        now=now,
        measurements=measurements,
        work_shares=_shares(7),
        regimes=[SEPTEMBER, OCTOBER],
    )
    assert [point.plan_oil for point in profile.points] == [
        2.0,
        2.0,
        2.0,
        4.0,
        4.0,
        4.0,
        4.0,
    ]
    # 3 полных сентябрьских дня по 2 и 3,5 октябрьских по 4.
    assert profile.plan_oil == round((3 * 2.0 + 3.5 * 4.0) / 6.5, 2)


def test_same_start_regimes_take_last_by_id() -> None:
    # Список — по (start_date, id); из режимов с одной датой — последний.
    first = PlanRegime(date(2026, 10, 1), date(2026, 10, 31), 3.0)
    second = PlanRegime(date(2026, 10, 1), date(2026, 10, 31), 5.0)
    assert plan_on([SEPTEMBER, first, second], date(2026, 10, 10)) == 5.0


def test_old_regime_is_plan_until_new_arrives() -> None:
    now = datetime(2026, 10, 4, 12, 0)
    profile = build_profile(
        ProfilePeriod.DAY,
        now=now,
        measurements=[],
        work_shares=_shares(24),
        regimes=[SEPTEMBER],
    )
    assert profile.plan_oil == 2.0


def test_work_share_falls_back_to_abai_per_hour() -> None:
    hours = hour_buckets(datetime(2026, 10, 20, 10), datetime(2026, 10, 20, 13))
    sdmo = {
        datetime(2026, 10, 20, 10): (16, 8),
        datetime(2026, 10, 20, 11): (1, 1),  # станция почти молчала
    }
    intervals = [
        (1, datetime(2026, 10, 1), datetime(2026, 10, 20, 11, 30), "WRK", "", None),
        (1, datetime(2026, 10, 20, 11, 30), FAR, "DWN", "В простое", None),
    ]
    shares = hourly_work_shares(hours, sdmo_counts=sdmo, abai_intervals=intervals)
    assert shares == [
        WorkShare(0.5, SOURCE_SDMO),
        WorkShare(0.5, SOURCE_ABAI),
        WorkShare(0.0, SOURCE_ABAI),
    ]
    assert hourly_work_shares(hours[:1], sdmo_counts={}, abai_intervals=[]) == [None]


def test_current_hour_waits_for_sdmo_loader_instead_of_abai() -> None:
    # 14:05: по текущему часу отсчётов СДМО ещё нет, в прошлом часе были —
    # «данных ещё нет», а не «В работе» из ABAI.
    now = datetime(2026, 10, 20, 14, 5)
    hours = hour_buckets(datetime(2026, 10, 20, 13), now)
    working_abai = [(1, datetime(2026, 10, 1), FAR, "WRK", "В работе", None)]
    shares = hourly_work_shares(
        hours,
        sdmo_counts={datetime(2026, 10, 20, 13): (17, 0)},
        abai_intervals=working_abai,
    )
    assert shares == [WorkShare(0.0, SOURCE_SDMO), None]
    # Станция молчит и в прошлом часе — тогда ABAI.
    shares = hourly_work_shares(hours, sdmo_counts={}, abai_intervals=working_abai)
    assert shares == [WorkShare(1.0, SOURCE_ABAI), WorkShare(1.0, SOURCE_ABAI)]
    # Неполный час: порог пропорционально меньше — одного отсчёта за 5 минут хватает.
    shares = hourly_work_shares(
        hours,
        sdmo_counts={
            datetime(2026, 10, 20, 13): (17, 0),
            datetime(2026, 10, 20, 14): (1, 0),
        },
        abai_intervals=working_abai,
    )
    assert shares == [WorkShare(0.0, SOURCE_SDMO), WorkShare(0.0, SOURCE_SDMO)]


def test_stopped_all_day_is_zero_while_sdmo_lags() -> None:
    # Сценарий ревью: стоит все сутки по СДМО, в ABAI висит WRK, сейчас 14:05.
    now = datetime(2026, 10, 20, 14, 5)
    _step, buckets = period_buckets(ProfilePeriod.DAY, now)
    hours = hour_buckets(buckets[0].start - timedelta(hours=1), now)
    sdmo = {hour.start: (17, 0) for hour in hours[:-1]}
    hourly = hourly_work_shares(
        hours,
        sdmo_counts=sdmo,
        abai_intervals=[(1, datetime(2026, 10, 1), FAR, "WRK", "В работе", None)],
    )
    profile = build_profile(
        ProfilePeriod.DAY,
        now=now,
        measurements=[_m(datetime(2026, 10, 18, 6), 8.0)],
        work_shares=point_work_shares(buckets, hours[1:], hourly[1:]),
        regimes=[OCTOBER],
    )
    assert profile.fact_oil == 0.0
    assert profile.work_share_source == SOURCE_SDMO
    assert profile.points[-1].work_share is None


def test_day_share_is_built_from_hours() -> None:
    # Станция 4 часа «работает» и замолчала (питание) — остальные часы по ABAI.
    day = Bucket(datetime(2026, 10, 19), datetime(2026, 10, 20))
    hours = hour_buckets(day.start, day.end)
    sdmo = {hour.start: (17, 17) for hour in hours[:4]}
    intervals = [
        (1, datetime(2026, 10, 1), datetime(2026, 10, 19, 4), "WRK", "", None),
        (1, datetime(2026, 10, 19, 4), FAR, "DWN", "Отключение", None),
    ]
    hourly = hourly_work_shares(hours, sdmo_counts=sdmo, abai_intervals=intervals)
    [share] = point_work_shares([day], hours, hourly)
    assert share == WorkShare(4 / 24, SOURCE_MIXED)


def test_mixed_sources() -> None:
    measurements = [_m(datetime(2026, 10, 20, 6), 8.0)]
    shares = _shares(12) + [WorkShare(1.0, SOURCE_ABAI)] * 12
    profile = build_profile(
        ProfilePeriod.DAY,
        now=NOW,
        measurements=measurements,
        work_shares=shares,
        regimes=[OCTOBER],
    )
    assert profile.work_share_source == SOURCE_MIXED


def test_abai_work_share_by_covered_time() -> None:
    since, until = datetime(2026, 10, 1), datetime(2026, 10, 21)
    intervals = [
        (1, datetime(2026, 9, 2), datetime(2026, 10, 11), "WRK", "В работе", None),
        (1, datetime(2026, 10, 11), FAR, "DWN", "В простое", "Нет подачи"),
    ]
    assert abai_work_share(intervals, since=since, until=until) == 0.5
    periodic = [(1, datetime(2026, 9, 1), FAR, "PEXP", "Периодическая", None)]
    assert abai_work_share(periodic, since=since, until=until) == 1.0
    assert abai_work_share([], since=since, until=until) is None


def test_point_before_first_measurement_is_gap() -> None:
    # Замеров раньше нет совсем — точки до первого замера без факта.
    measurements = [_m(datetime(2026, 10, 20, 6, 10), 8.0)]
    profile = build_profile(
        ProfilePeriod.DAY,
        now=NOW,
        measurements=measurements,
        work_shares=_shares(24),
        regimes=[OCTOBER],
    )
    facts = [point.fact_oil for point in profile.points]
    first = facts.index(8.0)
    assert profile.points[first].time == datetime(2026, 10, 20, 6)
    assert facts[:first] == [None] * first
    assert profile.fact_oil == 8.0


def test_measurements_list_is_period_only() -> None:
    measurements = [
        _m(NOW - timedelta(days=3), 8.0),
        _m(NOW - timedelta(hours=2), 9.0),
    ]
    profile = build_profile(
        ProfilePeriod.DAY,
        now=NOW,
        measurements=measurements,
        work_shares=_shares(24),
        regimes=[OCTOBER],
    )
    assert [item.oil for item in profile.measurements] == [9.0]
    # Дебит точек до замера в периоде — по старому замеру (медиана трёх).
    assert profile.points[0].fact_oil == 8.0
    assert profile.points[-1].fact_oil == 8.5


class _Repo:
    def __init__(self, **methods: object) -> None:
        self.calls: dict[str, dict] = {}
        for name, result in methods.items():
            setattr(self, name, self._method(name, result))

    def _method(self, name: str, result: object):  # noqa: ANN202
        async def method(*args: object, **kwargs: object) -> object:
            self.calls[name] = {"args": args, **kwargs}
            return result

        return method


def test_service_reads_inputs_for_period() -> None:
    now = datetime(2026, 10, 20, 14, 37)
    telemetry = _Repo(
        list_by_well_id_in_period=[
            SimpleNamespace(
                date_time=datetime(2026, 10, 20, 6),
                qm_oil=8.0,
                qv_liquid=20.0,
            ),
        ],
    )
    regimes = _Repo(
        list_by_abai_well_id_in_period=[
            SimpleNamespace(
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 31),
                oil=4.0,
            ),
        ],
    )
    stations = _Repo(
        list_by_well_id=[
            SimpleNamespace(id=7, active=True),
            SimpleNamespace(id=8, active=False),  # неактивная тоже считается
        ],
    )
    sdmo = _Repo(
        drive_running_by_hour={
            datetime(2026, 10, 19, 14) + timedelta(hours=index): (17, 17)
            for index in range(25)
        },
    )
    statuses = _Repo(list_intervals_by_abai_well_ids=[])
    service = WellProductionProfileService(
        telemetry_repository=telemetry,
        tech_regime_repository=regimes,
        sdmo_station_repository=stations,
        sdmo_fc_data_repository=sdmo,
        well_status_repository=statuses,
    )
    profile = asyncio.run(
        service.get(well_id=1, abai_well_id=100, period=ProfilePeriod.DAY, now=now),
    )
    assert profile.fact_oil == 8.0
    assert profile.plan_oil == 4.0
    assert profile.work_share == 1.0
    # Замеры — с запасом 60 суток до начала периода, до текущего момента.
    reads = telemetry.calls["list_by_well_id_in_period"]
    assert reads["date_time_from"] == datetime(2026, 10, 19, 15) - timedelta(days=60)
    assert reads["date_time_to"] == now
    # СДМО и ABAI — с часа до начала периода (отставание загрузчика).
    hourly = sdmo.calls["drive_running_by_hour"]
    assert hourly["args"][0] == [7, 8]
    assert hourly["since"] == datetime(2026, 10, 19, 14)
    assert hourly["until"] == now
    abai = statuses.calls["list_intervals_by_abai_well_ids"]
    assert abai["since"] == datetime(2026, 10, 19, 14)
    assert (
        regimes.calls["list_by_abai_well_id_in_period"]["start_date_to"] == now.date()
    )
