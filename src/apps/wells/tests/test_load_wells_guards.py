"""Скважины и координаты: пустой ответ ABAI не стирает фонд."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.wells.tasks.load_wells.load_coords import LoadCoords
from apps.wells.tasks.load_wells.load_wells import LoadWells, WellsSourceShrunkError


def _loader(source: list[int], local: list[int]) -> LoadWells:
    loader = LoadWells(abai_session=MagicMock(), app_session=MagicMock())
    loader.abai_wells_repo = MagicMock(
        get_list=AsyncMock(
            return_value=[SimpleNamespace(id=i, uwi=f"W_{i}") for i in source],
        ),
    )
    loader.well_repo = MagicMock(
        get_list=AsyncMock(
            return_value=[
                SimpleNamespace(abai_id=i, name=f"W_{i}", is_deleted=False)
                for i in local
            ],
        ),
        batch_create=AsyncMock(),
        mark_deleted_by_abai_ids=AsyncMock(),
        update_names_by_abai_id=AsyncMock(),
    )
    loader.app_session = MagicMock(commit=AsyncMock())
    return loader


def test_empty_abai_answer_does_not_delete_fund() -> None:
    loader = _loader(source=[], local=list(range(1, 201)))

    with pytest.raises(WellsSourceShrunkError):
        asyncio.run(loader.run())

    loader.well_repo.mark_deleted_by_abai_ids.assert_not_awaited()
    loader.app_session.commit.assert_not_awaited()


def test_few_deleted_wells_are_marked() -> None:
    loader = _loader(source=list(range(1, 199)), local=list(range(1, 201)))

    asyncio.run(loader.run())

    first = loader.well_repo.mark_deleted_by_abai_ids.await_args_list[0]
    assert sorted(first.args[0]) == [199, 200]


def test_wells_missing_in_source_keep_coords() -> None:
    loader = LoadCoords(abai_session=MagicMock(), app_session=MagicMock())
    loader.well_repo = MagicMock(
        get_list=AsyncMock(
            return_value=[
                SimpleNamespace(abai_id=1, coords_id=5),
                SimpleNamespace(abai_id=2, coords_id=6),
            ],
        ),
        update_coords_by_abai_ids=AsyncMock(),
    )

    changed = asyncio.run(
        loader._bind_wells({2: None}, known_coord_ids={5, 6}),  # noqa: SLF001
    )

    # Скважины 1 нет в ответе — координаты не трогаем; у 2 устья нет — снимаем.
    assert changed == 1
    loader.well_repo.update_coords_by_abai_ids.assert_awaited_once_with({2: None})
