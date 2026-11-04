"""Ремонты: сбой ответа ABAI не помечает и не теряет ремонты."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from apps.repairs.tasks.load_repairs.load_repairs import LoadRepairs
from apps.repairs.tasks.load_repairs.update_not_finished_repairs import (
    UpdateNotFinishedRepairs,
    too_many_gone,
)
from shared.integrations.abai.models import WellWorkover

NOW = datetime(2026, 11, 4, 5, 0)  # noqa: DTZ001


def _workover(abai_id: int, well: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=abai_id,
        well=well,
        work_list=None,
        work_plan=None,
        repair_work_type=7,
        repair_type=3,
        dbeg=NOW,
        dend=None,
    )


def test_too_many_gone() -> None:
    assert not too_many_gone(3, 30)  # единицы — настоящие удаления
    assert too_many_gone(4, 30)
    assert not too_many_gone(8, 80)
    assert too_many_gone(79, 80)  # пришёл 1 из 80 — сбой


def test_mass_gone_marks_nothing() -> None:
    repo = MagicMock(update_by_abai_id=AsyncMock())

    marked = asyncio.run(
        UpdateNotFinishedRepairs()._mark_gone(  # noqa: SLF001
            repo,
            list(range(70)),
            checked=80,
            now=NOW,
        ),
    )

    assert marked == 0
    repo.update_by_abai_id.assert_not_awaited()


def test_repair_back_in_abai_is_unmarked() -> None:
    repo = MagicMock(
        get_list=AsyncMock(
            return_value=[SimpleNamespace(abai_id=1), SimpleNamespace(abai_id=2)],
        ),
        update_by_abai_id=AsyncMock(),
    )
    abai = MagicMock(list_by_ids=AsyncMock(return_value=[SimpleNamespace(id=2)]))

    unmarked = asyncio.run(
        UpdateNotFinishedRepairs()._unmark_found(repo, abai),  # noqa: SLF001
    )

    assert unmarked == 1
    call = repo.update_by_abai_id.await_args
    assert call.kwargs["abai_id"] == 2
    assert call.kwargs["data"].abai_deleted_at is None


def test_missing_repairs_of_known_wells_are_restored() -> None:
    abai = MagicMock(
        model=WellWorkover,
        get_list=AsyncMock(
            return_value=[_workover(1, 10), _workover(2, 10), _workover(3, 99)],
        ),
    )
    repo = MagicMock(
        list_existing_abai_ids=AsyncMock(return_value={1}),
        bulk_create=AsyncMock(),
    )

    restored = asyncio.run(
        LoadRepairs()._restore_missing(repo, abai, {10}, since=NOW),  # noqa: SLF001
    )

    # 1 уже есть, 3 — по скважине, которой у нас нет.
    assert restored == 1
    assert [dto.abai_id for dto in repo.bulk_create.await_args.kwargs["data"]] == [2]
