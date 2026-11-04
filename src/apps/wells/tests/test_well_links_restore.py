"""Привязки к оргструктуре: строки скважины, заведённой позже, дочитываются."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from apps.wells.tasks.load_well_orgs.load_well_orgs import LoadWellOrgs


def test_rows_of_newly_added_well_are_restored() -> None:
    loader = LoadWellOrgs(abai_session=MagicMock(), app_session=MagicMock())
    loader.known_well_ids = {7}
    loader.well_org_repo = MagicMock(
        list_well_abai_ids_without_rows=AsyncMock(return_value=[7, 8]),
        bulk_create=AsyncMock(),
    )
    loader.abai_well_org_repo = MagicMock(
        list_by_wells=AsyncMock(
            return_value=[
                SimpleNamespace(id=1, well=7, org=50, dbeg=None, dend=None),
                SimpleNamespace(id=2, well=7, org=None, dbeg=None, dend=None),
            ],
        ),
    )

    restored = asyncio.run(loader._restore_new_wells())  # noqa: SLF001

    assert restored == 1
    created = loader.well_org_repo.bulk_create.await_args.args[0]
    assert [(dto.abai_id, dto.abai_org_id) for dto in created] == [(1, 50)]
