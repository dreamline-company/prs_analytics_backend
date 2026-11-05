"""Время экранов нарушений CM приводится к наивному местному, фильтры — к aware;
фильтр по обработанным экранам."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.settings import get_settings
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
    to_aware,
    to_local_naive,
)

if TYPE_CHECKING:
    from shared.repository.sqlalchemy import QuerySpec


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


class _CapturingRepo(CMBrigadeErrorScreenRepository):
    def __init__(self) -> None:
        self.specs: list[QuerySpec] = []

    async def get_list(self, spec: QuerySpec | None = None) -> list:
        self.specs.append(spec)
        return []


def _where(*, processed_only: bool) -> list[str]:
    repo = _CapturingRepo()
    asyncio.run(
        repo.list_by_brigade_ids_in_range(
            [7],
            start_time=datetime(2026, 9, 20, 8, 0),  # noqa: DTZ001
            end_time=datetime(2026, 9, 22, 18, 0),  # noqa: DTZ001
            processed_only=processed_only,
        ),
    )
    return [str(f) for f in repo.specs[0].filters]


def test_processed_only_adds_is_processed_filter() -> None:
    assert not any("is_processed" in f for f in _where(processed_only=False))
    assert any("is_processed IS true" in f for f in _where(processed_only=True))
