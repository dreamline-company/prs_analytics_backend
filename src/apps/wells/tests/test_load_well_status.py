"""Загрузка статусов ABAI: сбой чтения источника не стирает копию."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from apps.wells.tasks.load_well_status.load_well_status import LoadWellStatus

NOW = datetime(2026, 11, 4, 1, 10)  # noqa: DTZ001
OPEN = datetime(3333, 12, 31)  # noqa: DTZ001
WELL = 7
STATUS = 1


def _row(abai_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=abai_id,
        abai_id=abai_id,
        well=WELL,
        abai_well_id=WELL,
        status=STATUS,
        reason=None,
        dbeg=datetime(2026, 10, 1),  # noqa: DTZ001
        dend=OPEN,
    )


def _loader(local_open: list[int], source: list[int]) -> LoadWellStatus:
    loader = LoadWellStatus(abai_session=MagicMock(), app_session=MagicMock())
    loader.known_well_ids = {WELL}
    loader.known_type_ids = {STATUS}
    loader.status_repo = MagicMock(
        list_open_intervals=AsyncMock(return_value=[_row(i) for i in local_open]),
        list_existing_abai_ids=AsyncMock(return_value=set(local_open)),
        delete_by_abai_ids=AsyncMock(),
        bulk_create=AsyncMock(),
        update_by_abai_id=AsyncMock(),
    )
    loader.abai_status_repo = MagicMock(
        list_by_ids=AsyncMock(
            side_effect=lambda ids: [_row(i) for i in ids if i in set(source)],
        ),
        list_ending_after=AsyncMock(return_value=[_row(i) for i in source]),
    )
    return loader


def test_empty_source_answer_deletes_nothing() -> None:
    loader = _loader(local_open=list(range(1, 2001)), source=[])

    updated, deleted = asyncio.run(loader._refresh_open_intervals(NOW))  # noqa: SLF001

    assert (updated, deleted) == (0, 0)
    loader.status_repo.delete_by_abai_ids.assert_not_awaited()


def test_single_interval_deleted_by_technologists_is_removed() -> None:
    loader = _loader(local_open=list(range(1, 2001)), source=list(range(2, 2001)))

    _updated, deleted = asyncio.run(loader._refresh_open_intervals(NOW))  # noqa: SLF001

    assert deleted == 1
    loader.status_repo.delete_by_abai_ids.assert_awaited_once_with([1])


def test_intervals_missing_in_copy_are_restored() -> None:
    loader = _loader(local_open=[1, 2], source=[1, 2, 3, 4])

    restored = asyncio.run(loader._restore_missing(NOW))  # noqa: SLF001

    assert restored == 2
    created = loader.status_repo.bulk_create.await_args.args[0]
    assert [dto.abai_id for dto in created] == [3, 4]
