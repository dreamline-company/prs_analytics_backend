"""Окна запроса трека: перечитка от курсора, бэкфилл без курсора, куски."""

from datetime import datetime, timedelta
from itertools import pairwise

from apps.gps.tasks.load_tracks.load_tracks import (
    HISTORY,
    REREAD,
    iter_chunks,
    track_window,
)

NOW = datetime(2026, 10, 19, 12, 0)  # noqa: DTZ001 — местное naive, как в БД
CURSOR = NOW - timedelta(minutes=15)


def test_window_rereads_before_cursor() -> None:
    assert track_window(CURSOR, now=NOW) == (CURSOR - REREAD, NOW)


def test_window_without_cursor_is_full_history() -> None:
    assert track_window(None, now=NOW) == (NOW - HISTORY, NOW)


def test_window_never_reaches_past_history() -> None:
    ancient = NOW - HISTORY - timedelta(days=30)
    assert track_window(ancient, now=NOW) == (NOW - HISTORY, NOW)


def test_nightly_reread_window() -> None:
    start, _ = track_window(CURSOR, now=NOW, reread=timedelta(hours=72))
    assert start == CURSOR - timedelta(hours=72)


def test_chunks_cover_window_without_gaps() -> None:
    start, end = NOW - HISTORY, NOW
    chunks = list(iter_chunks(start, end))

    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    assert all(a[1] == b[0] for a, b in pairwise(chunks))
    assert all(b - a <= timedelta(days=7) for a, b in chunks)
    assert len(chunks) == 26  # 180 суток / 7


def test_empty_window_has_no_chunks() -> None:
    assert list(iter_chunks(NOW, NOW)) == []
    assert list(iter_chunks(NOW, NOW - timedelta(hours=1))) == []
