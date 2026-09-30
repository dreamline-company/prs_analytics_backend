"""Статус бригады в матрице: «Работа [n]» по коду работы на скважине ремонта,
«ПРС» при открытом ремонте без кодов, иначе пусто.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from apps.org.use_cases.list_brigades_by_ngdu_id import ListBrigadesByNGDUIdUseCase
from apps.wells.dto.internal.well_matrix import WELL_STATUS_PRS

BRIGADES = [SimpleNamespace(id=i, name=f"Бригада №{i}", ngdu_id=5) for i in (1, 2, 3)]
WELLS = {
    1001: SimpleNamespace(id=11, abai_id=1001, name="BLG_0001"),
    1002: SimpleNamespace(id=12, abai_id=1002, name="BLG_0002"),
}


def _repair(rid: int, abai_well_id: int, end: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        abai_id=rid * 10,
        well_id=None,
        abai_well_id=abai_well_id,
        repair_type_id=1,
        work_list=None,
        work_plan=None,
        start_time=datetime(2026, 9, 20, 8, 0),  # noqa: DTZ001
        end_time=end,
        is_open=end is None,
    )


REPAIRS = {
    501: _repair(501, 1001, None),  # бригада 1: открытый ремонт, есть код работы
    502: _repair(502, 1002, None),  # бригада 2: открытый ремонт без СПО
    503: _repair(
        503,
        1002,
        datetime(2026, 9, 1),
    ),  # бригада 3: закрытый ремонт
}
LINKS = [
    SimpleNamespace(brigade_id=b, repair_id=r)
    for b, r in ((1, 501), (2, 502), (3, 503))
]


class _UniqueBrigades:
    async def list_by_ngdu_id(self, _ngdu_id: int) -> list:
        return BRIGADES


class _RepairBrigades:
    async def list_by_brigade_ids(self, _ids: list[int]) -> list:
        return LINKS


class _Repairs:
    async def list_by_ids(self, ids: list[int]) -> list:
        return [REPAIRS[i] for i in ids if i in REPAIRS]

    async def count_by_well_abai_ids_since(
        self,
        _ids: list[int],
        *,
        since: datetime,
    ) -> dict:
        del since
        return {}


class _Wells:
    async def list_by_abai_ids(self, ids: list[int]) -> list:
        return [WELLS[i] for i in ids if i in WELLS]


class _Spo:
    async def map_last_work_codes(
        self,
        since_by_well_id: dict[int, datetime],
    ) -> dict[int, int]:
        return {well_id: 3 for well_id in since_by_well_id if well_id == 11}


class _UseCase(ListBrigadesByNGDUIdUseCase):
    async def _compute_violations(self, *_args, **_kwargs) -> tuple[dict, dict]:  # noqa: ANN002, ANN003
        return {}, {}  # CM здесь не при чём


def test_brigade_status_work_code_prs_none() -> None:
    use_case = _UseCase(
        unique_brigade_repository=_UniqueBrigades(),  # type: ignore[arg-type]
        repair_brigade_repository=_RepairBrigades(),  # type: ignore[arg-type]
        repair_repository=_Repairs(),  # type: ignore[arg-type]
        well_repository=_Wells(),  # type: ignore[arg-type]
        cm_brigade_repository=None,  # type: ignore[arg-type]
        cm_brigade_error_screen_repository=None,  # type: ignore[arg-type]
        spo_repository=_Spo(),  # type: ignore[arg-type]
    )

    items = asyncio.run(use_case.execute(SimpleNamespace(ngdu_id=5)))  # type: ignore[arg-type]

    by_id = {i.id: i for i in items}
    assert by_id[1].status == "Работа [3]"
    assert by_id[1].legend is not None
    assert by_id[1].legend.status == "Работа [3]"
    assert by_id[2].status == WELL_STATUS_PRS
    assert by_id[2].is_in_repair is True
    assert by_id[3].status is None
    assert by_id[3].is_in_repair is False
