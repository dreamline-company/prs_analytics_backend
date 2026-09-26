"""Время экранов нарушений CM приводится к наивному местному, фильтры — к aware."""

from __future__ import annotations

from datetime import UTC, datetime

from core.settings import get_settings
from shared.integrations.cm.repositories.brigade_error_screens import (
    to_aware,
    to_local_naive,
)


def test_utc_aware_becomes_local_naive() -> None:
    zone = get_settings().ZONE_INFO
    utc = datetime(2026, 9, 12, 15, 30, 44, tzinfo=UTC)

    local = to_local_naive(utc)

    assert local.tzinfo is None
    assert local == utc.astimezone(zone).replace(tzinfo=None)
    assert to_local_naive(local) == local  # наивное не трогаем


def test_naive_local_becomes_aware_for_filters() -> None:
    zone = get_settings().ZONE_INFO
    naive = datetime(2026, 9, 19, 8, 0)  # noqa: DTZ001

    aware = to_aware(naive)

    assert aware.tzinfo is zone
    assert to_aware(aware) is aware
    # наивное местное <-> aware -> обратно даёт то же самое
    assert to_local_naive(aware) == naive
