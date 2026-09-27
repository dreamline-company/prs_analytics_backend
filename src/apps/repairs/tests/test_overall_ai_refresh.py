"""Общий вердикт пересчитывается при закрытии ремонта, новых нарушениях и
смене исхода AI-разбора СПО; упавший вердикт не замораживается финализацией."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.repairs.models.analytics import AI_STATUS_COMPLETED, AI_STATUS_FAILED
from apps.repairs.tasks.fill_analytics.fill_repair_analytics import FillRepairAnalytics
from apps.repairs.tasks.fill_analytics.inputs import overall_fingerprint

BASE = "abc"


def test_fingerprint_reacts_to_close_violations_and_spo_outcome() -> None:
    initial = overall_fingerprint(
        BASE, end_time=None, violations=[], spo_ai_outcomes={},
    )

    closed = overall_fingerprint(
        BASE,
        end_time=datetime(2026, 9, 20),  # noqa: DTZ001
        violations=[],
        spo_ai_outcomes={},
    )
    violated = overall_fingerprint(
        BASE,
        end_time=None,
        violations=[("2026-09-19T10:00:00", "нет каски")],
        spo_ai_outcomes={},
    )
    spo_failed = overall_fingerprint(
        BASE,
        end_time=None,
        violations=[],
        spo_ai_outcomes={7: (AI_STATUS_FAILED, "v1")},
    )
    spo_ok = overall_fingerprint(
        BASE,
        end_time=None,
        violations=[],
        spo_ai_outcomes={7: (AI_STATUS_COMPLETED, "v1")},
    )

    assert len({initial, closed, violated, spo_failed, spo_ok}) == 5
    # порядок нарушений не влияет
    a = overall_fingerprint(
        BASE, end_time=None, violations=[("t1", "a"), ("t2", "b")], spo_ai_outcomes={},
    )
    b = overall_fingerprint(
        BASE, end_time=None, violations=[("t2", "b"), ("t1", "a")], spo_ai_outcomes={},
    )
    assert a == b


class _Docs:
    def __init__(self, *, complete: bool) -> None:
        self._complete = complete

    async def get_by_repair_id(self, _repair_id: int) -> SimpleNamespace | None:
        if not self._complete:
            return None
        return SimpleNamespace(por_file_id=1, act_file_id=2)


def _finalize(*, end_days_ago: int | None, docs: bool, status: str) -> bool:
    now = datetime(2026, 9, 27, 12, 0)  # noqa: DTZ001
    end = now - timedelta(days=end_days_ago) if end_days_ago is not None else None
    repair = SimpleNamespace(id=1, end_time=end)
    return asyncio.run(
        FillRepairAnalytics._should_finalize(  # noqa: SLF001
            repair=repair,  # type: ignore[arg-type]
            doc_repo=_Docs(complete=docs),  # type: ignore[arg-type]
            grace_cutoff=now - timedelta(days=10),
            retry_cutoff=now - timedelta(days=40),
            overall_ai_status=status,
        ),
    )


def test_finalize_by_time_requires_completed_verdict_within_retry_window() -> None:
    assert _finalize(end_days_ago=15, docs=False, status=AI_STATUS_COMPLETED) is True
    assert _finalize(end_days_ago=15, docs=False, status=AI_STATUS_FAILED) is False
    assert _finalize(end_days_ago=45, docs=False, status=AI_STATUS_FAILED) is True
    assert _finalize(end_days_ago=None, docs=True, status=AI_STATUS_COMPLETED) is True
    assert _finalize(end_days_ago=None, docs=True, status=AI_STATUS_FAILED) is False
    assert _finalize(end_days_ago=2, docs=False, status=AI_STATUS_COMPLETED) is False
