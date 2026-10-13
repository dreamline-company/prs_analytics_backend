"""Когда опросчик перечитывает уже известный замер (``_needs_fetch``)."""

# ruff: noqa: DTZ001, SLF001 — метки замеров наивные, как в БД; проверяем _needs_fetch

from datetime import UTC, datetime, timedelta

from apps.kbrs.models.measure import MEASURE_STATUS_OK, MEASURE_STATUS_PARSE_ERROR
from apps.kbrs.tasks.poll_measures.poller import KbrsMeasurePoller, _KnownMeasure

GRACE = timedelta(minutes=120)
NOW = datetime(2026, 10, 13, 13, 0)


def _poller() -> KbrsMeasurePoller:
    return KbrsMeasurePoller(
        pool=None,
        storage=None,
        poll_interval_seconds=60,
        keepalive_interval_seconds=240,
        window_hours=24,
        refresh_grace_minutes=120,
        page_count=200,
        late_sweep_minutes=30,
        late_sweep_window_hours=72,
    )


def _known(
    *,
    status: str,
    end_time: datetime | None,
    fetched_ago: timedelta,
    created_ago: timedelta,
) -> _KnownMeasure:
    return _KnownMeasure(
        id=1,
        raw_size=1000,
        status=status,
        end_time=end_time,
        fetched_at=NOW - fetched_ago,
        raw_file_id=None,
        chart_json_file_id=None,
        notes_file_id=None,
        passport_file_id=None,
        # created_at — UTC, как now() БД на проде.
        created_at=datetime.now(UTC).replace(tzinfo=None) - created_ago,
    )


def test_new_measure_is_fetched() -> None:
    assert _poller()._needs_fetch(None, NOW)


def test_young_unparsed_measure_is_retried_every_poll() -> None:
    # Только что начатый замер: телеметрии ещё нет, разбор упал минуту назад.
    known = _known(
        status=MEASURE_STATUS_PARSE_ERROR,
        end_time=None,
        fetched_ago=timedelta(minutes=1),
        created_ago=timedelta(minutes=10),
    )
    assert _poller()._needs_fetch(known, NOW)


def test_old_unparsed_measure_is_retried_once_per_grace() -> None:
    old = timedelta(hours=5)
    recent = _known(
        status=MEASURE_STATUS_PARSE_ERROR,
        end_time=None,
        fetched_ago=timedelta(minutes=30),
        created_ago=old,
    )
    stale = _known(
        status=MEASURE_STATUS_PARSE_ERROR,
        end_time=None,
        fetched_ago=GRACE,
        created_ago=old,
    )
    assert not _poller()._needs_fetch(recent, NOW)
    assert _poller()._needs_fetch(stale, NOW)


def test_live_measure_is_refetched_closed_is_not() -> None:
    live = _known(
        status=MEASURE_STATUS_OK,
        end_time=NOW - timedelta(minutes=5),
        fetched_ago=timedelta(minutes=1),
        created_ago=timedelta(hours=3),
    )
    closed = _known(
        status=MEASURE_STATUS_OK,
        end_time=NOW - timedelta(hours=5),
        fetched_ago=timedelta(hours=3),
        created_ago=timedelta(hours=8),
    )
    assert _poller()._needs_fetch(live, NOW)
    # Закрытый разобранный — забота фонового прохода (late_sweep), не опроса.
    assert not _poller()._needs_fetch(closed, NOW)
