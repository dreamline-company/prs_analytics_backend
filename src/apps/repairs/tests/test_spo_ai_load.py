"""Нагрузка на ИИ от СПО: аналитика только на новую СПО, упавший разбор не
крутится по кругу, длинный график ужимается под контекст модели."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.repairs.models.analytics import AI_STATUS_COMPLETED, AI_STATUS_FAILED
from apps.repairs.tasks.fetch_sources import link_spo
from apps.repairs.tasks.fill_analytics.ai.coordinator import (
    SPO_AI_RETRY_AFTER,
    AICoordinator,
)
from apps.repairs.tasks.fill_analytics.ai.spo_processor import (
    COMPACT_HEADER,
    MAX_CHART_ROWS,
    compact_chart_csv,
)
from core.settings import get_settings

HEADER = "timestamp,datetime,hook_weight_t,h2s_mg_m3,ch4_percent"


def _chart(rows: int) -> str:
    lines = [HEADER]
    for i in range(rows):
        weight = 90.0 if i == 777 else 20.0 + (i % 7)  # один пик нагрузки
        gas = "3.5" if i == 1500 else ""
        lines.append(
            f"{i},2026-09-29T09:{i // 60 % 60:02d}:{i % 60:02d},{weight},{gas},",
        )
    return "\n".join(lines) + "\n"


def test_short_chart_goes_as_is() -> None:
    text = _chart(MAX_CHART_ROWS)

    assert compact_chart_csv(text) is text


def test_long_chart_is_compacted_without_losing_peaks() -> None:
    lines = compact_chart_csv(_chart(5543)).splitlines()

    assert lines[0] == COMPACT_HEADER
    assert len(lines) - 1 <= MAX_CHART_ROWS
    weight_max = max(float(line.split(",")[2]) for line in lines[1:])
    weight_min = min(float(line.split(",")[1]) for line in lines[1:])
    h2s = [line.split(",")[3] for line in lines[1:] if line.split(",")[3]]
    assert (weight_min, weight_max) == (20.0, 90.0)
    assert h2s == ["3.5"]


def _now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


@pytest.mark.parametrize(
    ("status", "version", "age", "skip"),
    [
        (AI_STATUS_FAILED, "v1", timedelta(minutes=10), True),
        (AI_STATUS_FAILED, "v1", SPO_AI_RETRY_AFTER + timedelta(minutes=1), False),
        (AI_STATUS_FAILED, "v0", timedelta(minutes=10), False),
        (AI_STATUS_COMPLETED, "v1", timedelta(minutes=10), False),
    ],
)
def test_failed_spo_ai_is_not_retried_every_run(
    status: str,
    version: str,
    age: timedelta,
    *,
    skip: bool,
) -> None:
    row = SimpleNamespace(
        status=status,
        prompt_version=version,
        processed_at=_now() - age,
    )

    assert AICoordinator._failed_recently(row, "v1") is skip  # noqa: SLF001


class _Session:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def __aenter__(self) -> "_Session":  # noqa: PYI034
        return self

    async def __aexit__(self, *_exc: object) -> None:
        pass


def test_analytics_only_for_new_spo(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduled: list[int] = []

    async def schedule(repair_id: int) -> bool:
        scheduled.append(repair_id)
        return True

    # ремонт 1 — живой замер дорос, ремонт 2 — появилась новая СПО
    outcome = {1: (True, False), 2: (True, True), 3: (False, False)}

    async def link_repair(
        _ctx: object,
        repair: SimpleNamespace,
        **_kw: object,
    ) -> tuple:
        return outcome[repair.id]

    async def target_repairs(_ctx: object, **_kw: object) -> list:
        return [SimpleNamespace(id=i) for i in outcome]

    monkeypatch.setattr(link_spo, "session_makers", {"app": _Session})
    monkeypatch.setattr(link_spo, "build_storage", lambda: None)
    monkeypatch.setattr(link_spo, "_Context", lambda *_a, **_kw: None)
    monkeypatch.setattr(link_spo, "schedule_repair_analytics", schedule)
    runner = link_spo.LinkRepairSpo(measure_id=251467)
    monkeypatch.setattr(runner, "_target_repairs", target_repairs)
    monkeypatch.setattr(runner, "_link_repair", link_repair)

    stats = asyncio.run(runner.run())

    assert scheduled == [2]
    assert stats.changed == 2
