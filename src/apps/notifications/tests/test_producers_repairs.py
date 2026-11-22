"""Начало ремонта: построитель и хук загрузчика ремонтов."""

# ruff: noqa: DTZ001, SLF001

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.notifications.producers.repairs import (
    EVENT_REPAIR_STARTED,
    recent_starts,
    repair_started_notification,
)
from apps.repairs.dto.internal.repair import StartedRepairDTO
from apps.repairs.tasks.load_repairs import load_repairs
from apps.repairs.tasks.load_repairs.load_repairs import LoadRepairs
from shared.integrations.abai.models import WellWorkover

NOW = datetime(2026, 11, 23, 10, 42)
START = datetime(2026, 11, 23, 9, 0)


def _started(**overrides: Any) -> StartedRepairDTO:  # noqa: ANN401
    data: dict[str, Any] = {
        "id": 7001,
        "abai_id": 91001,
        "start_time": START,
        "abai_repair_type_id": 3,
        "repair_type_name_ru": "TP 4-2 Устранение обрыва штанг",
        "well_id": 1,
        "well_name": "VMB_2051",
    }
    data.update(overrides)
    return StartedRepairDTO(**data)


def test_prs_repair_title_source_and_facts() -> None:
    item = repair_started_notification(_started())

    assert item.event_code == EVENT_REPAIR_STARTED
    assert item.severity == "info"
    assert item.title_ru == (
        "VMB_2051: начался ремонт (ПРС, TP 4-2 Устранение обрыва штанг)"
    )
    assert (item.source_type, item.source_id) == ("repair", 7001)
    assert item.dedup_key == "well.repair_started:repair:7001"
    assert item.well_id == 1
    # НГДУ эмиттер найдёт сам по скважине.
    assert item.abai_ngdu_id is None
    assert item.event_at == START
    facts = {fact.label_ru: fact.value_ru for fact in item.facts}
    assert facts == {
        "Начало": "23.11 09:00",
        "Вид работ": "TP 4-2 Устранение обрыва штанг",
        "Категория": "ПРС",
    }


def test_krs_and_unknown_category() -> None:
    krs = repair_started_notification(
        _started(abai_repair_type_id=1, repair_type_name_ru="КР-6 Ловильные работы"),
    )
    assert krs.title_ru == "VMB_2051: начался ремонт (КРС, КР-6 Ловильные работы)"

    bare = repair_started_notification(
        _started(abai_repair_type_id=None, repair_type_name_ru=None),
    )
    assert bare.title_ru == "VMB_2051: начался ремонт"
    assert [fact.label_ru for fact in bare.facts] == ["Начало"]


def test_long_work_type_is_clipped_to_title_limit() -> None:
    item = repair_started_notification(_started(repair_type_name_ru="я" * 400))
    assert len(item.title_ru) == 255
    assert item.title_ru.endswith("…")


def test_only_recent_starts_are_notified() -> None:
    starts = [
        (1, NOW - timedelta(hours=2)),
        (2, NOW - timedelta(days=3)),  # внесли в ABAI задним числом
        (3, None),
        (4, NOW + timedelta(hours=1)),  # ещё не начался
        (5, NOW),
    ]
    assert recent_starts(starts, NOW) == [1, 5]


class _Session:
    def __init__(self) -> None:
        self.savepoints = 0

    @asynccontextmanager
    async def begin_nested(self) -> Any:  # noqa: ANN401
        self.savepoints += 1
        yield


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list:
    items: list = []

    async def emit_all(_session: object, batch: list) -> list[int]:
        items.extend(batch)
        return [100 + i for i in range(len(batch))]

    monkeypatch.setattr(load_repairs, "emit_all", emit_all)
    return items


def _repo(monkeypatch: pytest.MonkeyPatch, rows: list | Exception) -> AsyncMock:
    method = (
        AsyncMock(side_effect=rows)
        if isinstance(rows, Exception)
        else AsyncMock(
            return_value=rows,
        )
    )
    monkeypatch.setattr(
        load_repairs,
        "RepairRepository",
        lambda _session: SimpleNamespace(list_started_by_abai_ids=method),
    )
    return method


def test_emit_started_reads_ids_and_writes_notifications(
    monkeypatch: pytest.MonkeyPatch,
    emitted: list,
) -> None:
    method = _repo(monkeypatch, [_started()])
    session = _Session()

    ids = asyncio.run(LoadRepairs(notify=True)._emit_started(session, [91001]))

    assert ids == [100]
    method.assert_awaited_once_with([91001])
    assert session.savepoints == 1
    assert [item.source_id for item in emitted] == [7001]


def test_no_notify_or_nothing_recent_reads_nothing(
    monkeypatch: pytest.MonkeyPatch,
    emitted: list,
) -> None:
    method = _repo(monkeypatch, [_started()])
    assert asyncio.run(LoadRepairs()._emit_started(_Session(), [91001])) == []
    assert asyncio.run(LoadRepairs(notify=True)._emit_started(_Session(), [])) == []
    method.assert_not_awaited()
    assert emitted == []


def test_notification_failure_does_not_break_loading(
    monkeypatch: pytest.MonkeyPatch,
    emitted: list,
) -> None:
    _repo(monkeypatch, RuntimeError("db down"))
    ids = asyncio.run(LoadRepairs(notify=True)._emit_started(_Session(), [91001]))
    assert ids == []
    assert emitted == []


def test_restored_repairs_are_remembered_for_notifications() -> None:
    def workover(abai_id: int, well: int, dbeg: datetime) -> SimpleNamespace:
        return SimpleNamespace(
            id=abai_id,
            well=well,
            work_list=None,
            work_plan=None,
            repair_work_type=7,
            repair_type=3,
            dbeg=dbeg,
            dend=None,
        )

    abai = MagicMock(
        model=WellWorkover,
        get_list=AsyncMock(
            return_value=[workover(1, 10, START), workover(2, 10, START)],
        ),
    )
    repo = MagicMock(
        list_existing_abai_ids=AsyncMock(return_value={1}),
        bulk_create=AsyncMock(),
    )
    loader = LoadRepairs(notify=True)

    restored = asyncio.run(loader._restore_missing(repo, abai, {10}, since=NOW))

    assert restored == 1
    assert loader.restored_starts == [(2, START)]
