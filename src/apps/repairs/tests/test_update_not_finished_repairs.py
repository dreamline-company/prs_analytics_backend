"""Актуализация идущих ремонтов по ABAI: закрытие без ограничения давности и
пометка ремонтов, пропавших из ABAI."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.repairs.models.repair import Repair
from apps.repairs.tasks.load_repairs import update_not_finished_repairs as module

CLOSED = datetime(2024, 5, 14, 15, 46)  # noqa: DTZ001


def test_open_means_not_finished_and_not_deleted() -> None:
    assert Repair(end_time=None, abai_deleted_at=None).is_open
    assert not Repair(end_time=CLOSED, abai_deleted_at=None).is_open
    assert not Repair(end_time=None, abai_deleted_at=CLOSED).is_open
    sql = str(select(Repair).where(Repair.is_open))
    assert "end_time IS NULL" in sql
    assert "abai_deleted_at IS NULL" in sql


class _Session:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def __aenter__(self) -> "_Session":  # noqa: PYI034
        return self

    async def __aexit__(self, *_exc: object) -> None:
        pass


class _AppRepairs:
    model = Repair

    def __init__(self, open_abai_ids: list[int]) -> None:
        self._batches = [[SimpleNamespace(abai_id=i) for i in open_abai_ids], []]
        self.updates: dict[int, dict] = {}

    async def get_list(self, spec: object) -> list:  # noqa: ARG002
        return self._batches.pop(0)

    async def update_by_abai_id(self, abai_id: int, data: object) -> None:
        self.updates[abai_id] = data.model_dump(exclude_unset=True)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    open_abai_ids: list[int],
    in_abai: list[SimpleNamespace],
) -> dict[int, dict]:
    app = _AppRepairs(open_abai_ids)

    class _AbaiRepairs:
        def __init__(self, _session: object) -> None:
            pass

        async def list_by_ids(self, _ids: list[int]) -> list:
            return in_abai

    monkeypatch.setattr(module, "session_makers", {"app": _Session, "abai": _Session})
    monkeypatch.setattr(module, "RepairRepository", lambda _session: app)
    monkeypatch.setattr(module, "ABAIWellWorkoverRepository", _AbaiRepairs)
    asyncio.run(module.UpdateNotFinishedRepairs().run())
    return app.updates


def test_closed_in_abai_and_gone_from_abai(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = _run(
        monkeypatch,
        open_abai_ids=[5880941, 7506638, 5719639],
        in_abai=[
            SimpleNamespace(id=5880941, dend=CLOSED, work_plan=None, work_list=None),
            SimpleNamespace(id=7506638, dend=None, work_plan=None, work_list=None),
        ],
    )

    assert updates[5880941]["end_time"] == CLOSED  # VMB_1145: закрыт в ABAI
    # закрытый нами по телеметрии получает дату ABAI и теряет пометку
    assert updates[5880941]["closed_by_telemetry_at"] is None
    assert 7506638 not in updates  # VMB_2507: идёт и в ABAI
    assert set(updates[5719639]) == {"abai_deleted_at"}  # UZV_5555: удалён в ABAI


def test_empty_abai_answer_marks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, open_abai_ids=[1, 2], in_abai=[]) == {}
