"""Проход ИИ-разбора по фонду: курсор страниц, лимит, бюджет, счётчики."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar, Self

import pytest

from apps.repairs.models.analytics import AI_STATUS_COMPLETED, AI_STATUS_FAILED
from apps.repairs.tasks.fill_analytics.ai.base import TokenUsage
from apps.wells.tasks.analyze_dynamograms import analyze_dynamograms as module
from apps.wells.tasks.analyze_dynamograms.analyze_dynamograms import (
    AnalyzeDynamograms,
)

BASE = datetime(2026, 10, 1)  # noqa: DTZ001
# id → исход разбора; snapshot_time растёт с id, проход идёт от свежих.
OUTCOMES = {
    1: AI_STATUS_COMPLETED,
    2: AI_STATUS_FAILED,
    3: "busy",
    4: "error",
    5: AI_STATUS_COMPLETED,
}
DYNAMOGRAMS = [
    SimpleNamespace(id=i, snapshot_time=BASE + timedelta(days=i)) for i in OUTCOMES
]


class _Session:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None: ...

    async def commit(self) -> None: ...


class _Repo:
    afters: ClassVar[list] = []

    def __init__(self, _session: object) -> None: ...

    async def list_dynamograms_to_analyze(
        self,
        *,
        after: tuple[datetime, int] | None,
        limit: int,
        **_kwargs: object,
    ) -> list[tuple[SimpleNamespace, int]]:
        _Repo.afters.append(after)
        rows = sorted(DYNAMOGRAMS, key=lambda d: (d.snapshot_time, d.id), reverse=True)
        if after is not None:
            rows = [d for d in rows if (d.snapshot_time, d.id) < after]
        return [(d, 100 + d.id) for d in rows[:limit]]


class _Analyzer:
    analyzed: ClassVar[list[int]] = []

    async def analyze(
        self,
        dynamogram: SimpleNamespace,
        *,
        abai_well_id: int,
        lock_wait_sec: float,
    ) -> tuple[SimpleNamespace | None, TokenUsage]:
        assert lock_wait_sec == 0  # проход по фонду замок не ждёт
        assert abai_well_id == 100 + dynamogram.id
        _Analyzer.analyzed.append(dynamogram.id)
        outcome = OUTCOMES[dynamogram.id]
        if outcome == "error":
            msg = "db down"
            raise RuntimeError(msg)
        if outcome == "busy":
            return None, TokenUsage()
        return SimpleNamespace(status=outcome), TokenUsage(total_tokens=10)


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    _Repo.afters = []
    _Analyzer.analyzed = []
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    monkeypatch.setattr(module, "session_makers", {"app": _Session})
    monkeypatch.setattr(module, "RepairDynamogramAIResultRepository", _Repo)
    monkeypatch.setattr(
        module,
        "build_dynamogram_processor",
        lambda: SimpleNamespace(prompt_version="v3"),
    )
    monkeypatch.setattr(module, "build_storage", lambda: None)
    monkeypatch.setattr(
        module,
        "build_dynamogram_analyzer",
        lambda *_args, **_kwargs: _Analyzer(),
    )


def test_pass_walks_pages_newest_first_and_counts_outcomes() -> None:
    result = asyncio.run(AnalyzeDynamograms().run())

    assert _Analyzer.analyzed == [5, 4, 3, 2, 1]
    # Курсор — последняя строка прошлой страницы; упавшие и занятые строки
    # не возвращаются на следующей странице.
    assert _Repo.afters == [
        None,
        (DYNAMOGRAMS[3].snapshot_time, 4),
        (DYNAMOGRAMS[1].snapshot_time, 2),
        (DYNAMOGRAMS[0].snapshot_time, 1),
    ]
    assert (result.completed, result.failed, result.busy, result.errors) == (
        2,
        1,
        1,
        1,
    )
    assert result.tokens == 30
    assert result.deferred is False


def test_limit_stops_pass_and_marks_it_deferred() -> None:
    result = asyncio.run(AnalyzeDynamograms(limit=3).run())

    assert _Analyzer.analyzed == [5, 4, 3]
    assert result.deferred is True


def test_concurrency_defaults_to_setting_and_is_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(DYNAMOGRAM_AI_FUND_CONCURRENCY=2),
    )
    assert AnalyzeDynamograms()._concurrency == 2  # noqa: SLF001
    assert AnalyzeDynamograms(concurrency=50)._concurrency == module.MAX_CONCURRENCY  # noqa: SLF001


def test_exhausted_budget_defers_without_analysis() -> None:
    result = asyncio.run(AnalyzeDynamograms(time_budget_sec=0).run())

    assert _Analyzer.analyzed == []
    assert result.deferred is True
