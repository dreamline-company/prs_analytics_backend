"""Статус скважины в матрице: СПО при живом замере, ПРС при ремонте, иначе пусто."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

from apps.wells.dto.internal.well_matrix import WELL_STATUS_PRS, WELL_STATUS_SPO
from apps.wells.use_cases.get_wells_matrix import GetWellsMatrixUseCase

if TYPE_CHECKING:
    from collections.abc import Sequence

WELLS = [
    SimpleNamespace(id=1, abai_id=1001, name="BLG_0001"),  # ремонт + живая СПО
    SimpleNamespace(id=2, abai_id=1002, name="BLG_0002"),  # только ремонт
    SimpleNamespace(id=3, abai_id=1003, name="BLG_0003"),  # ничего
]


def _repair(rid: int, abai_well_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        abai_id=rid * 10,
        well_id=None,
        abai_well_id=abai_well_id,
        repair_type_id=1,
        work_list=None,
        work_plan=None,
        start_time=datetime(2026, 9, 20, 8, 0),  # noqa: DTZ001
        end_time=None,
    )


class _NgduWells:
    async def list_wells(self, _ngdu_id: int) -> list[SimpleNamespace]:
        return WELLS


class _Repairs:
    async def list_active_by_well_abai_ids(self, _ids: list[int]) -> list:
        return [_repair(501, 1001), _repair(502, 1002)]

    async def count_by_well_abai_ids_since(
        self,
        _ids: list[int],
        *,
        since: datetime,
    ) -> dict:
        del since  # порог частых ремонтов в этом тесте не важен
        return {}


class _RepairBrigades:
    async def list_by_repair_ids(self, _ids: list[int]) -> list:
        return []


class _Spo:
    def __init__(self) -> None:
        self.calls: list[tuple[list[int], datetime, timedelta]] = []

    async def list_well_ids_with_live_measures(
        self,
        well_ids: Sequence[int],
        *,
        now: datetime,
        grace: timedelta,
    ) -> set[int]:
        self.calls.append((sorted(well_ids), now, grace))
        return {1}


def _use_case(spo: _Spo) -> GetWellsMatrixUseCase:
    return GetWellsMatrixUseCase(
        ngdu_wells_service=_NgduWells(),  # type: ignore[arg-type]
        repair_repository=_Repairs(),  # type: ignore[arg-type]
        repair_brigade_repository=_RepairBrigades(),  # type: ignore[arg-type]
        unique_brigade_repository=None,  # type: ignore[arg-type]
        cm_brigade_repository=None,  # type: ignore[arg-type]
        cm_brigade_error_screen_repository=None,  # type: ignore[arg-type]
        spo_repository=spo,  # type: ignore[arg-type]
    )


def test_status_spo_over_prs_over_nothing() -> None:
    spo = _Spo()
    query = SimpleNamespace(ngdu_id=4)

    items = asyncio.run(_use_case(spo).execute(query))  # type: ignore[arg-type]

    by_name = {i.name: i for i in items}
    assert by_name["BLG_0001"].status == WELL_STATUS_SPO
    assert by_name["BLG_0001"].is_on_repair is True
    assert by_name["BLG_0002"].status == WELL_STATUS_PRS
    assert by_name["BLG_0003"].status is None
    assert by_name["BLG_0003"].is_on_repair is False
    # репозиторий СПО спрашивают по локальным id всех скважин НГДУ
    assert spo.calls
    assert spo.calls[0][0] == [1, 2, 3]
