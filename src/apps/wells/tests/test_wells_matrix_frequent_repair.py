"""Флаг «частые ремонты» в матрице совпадает со списком ЧРФ в сводке."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.repairs.services.mrp import RepairSpan, WellFundInput
from apps.wells.use_cases.get_wells_matrix import GetWellsMatrixUseCase

WELLS = [
    SimpleNamespace(id=1, abai_id=1001, name="VMB_0001"),  # 3 ремонта без ГТМ
    SimpleNamespace(id=2, abai_id=1002, name="VMB_0002"),  # 3 ремонта, один ГТМ
    SimpleNamespace(id=3, abai_id=1003, name="VMB_0003"),  # 2 ремонта
]


class _NgduWells:
    async def list_wells(self, _ngdu_id: int) -> list[SimpleNamespace]:
        return WELLS


class _Repairs:
    async def list_active_by_well_abai_ids(self, _ids: list[int]) -> list:
        return []


class _RepairBrigades:
    async def list_by_repair_ids(self, _ids: list[int]) -> list:
        return []


class _Spo:
    async def map_last_work_codes(self, _since: dict[int, datetime]) -> dict[int, int]:
        return {}


def _span(rid: int, day: datetime, *, gtm: bool = False) -> RepairSpan:
    return RepairSpan(rid, day, day + timedelta(days=1), is_gtm=gtm)


class _MrpInputs:
    """Три месяца назад — внутри скользящего года до прошлого полного месяца."""

    async def load_fund_inputs(self, wells: list, **_kwargs: object) -> list:
        base = datetime.now().replace(day=10, hour=8, minute=0, second=0, microsecond=0)  # noqa: DTZ005
        days = [base - timedelta(days=30 * n) for n in (2, 3, 4)]
        repairs = {
            "VMB_0001": [_span(i, d) for i, d in enumerate(days)],
            "VMB_0002": [
                _span(10, days[0]),
                _span(11, days[1]),
                _span(12, days[2], gtm=True),
            ],
            "VMB_0003": [_span(20, days[0]), _span(21, days[1])],
        }
        return [
            WellFundInput(
                well_id=w.id,
                name=w.name,
                oil_field="VMB",
                lift_type="ЭВН",
                work_intervals=[],
                repairs=repairs[w.name],
                fund_months=None,
            )
            for w in wells
        ]


def test_matrix_flag_follows_the_crf_rule() -> None:
    use_case = GetWellsMatrixUseCase(
        ngdu_wells_service=_NgduWells(),  # type: ignore[arg-type]
        repair_repository=_Repairs(),  # type: ignore[arg-type]
        repair_brigade_repository=_RepairBrigades(),  # type: ignore[arg-type]
        unique_brigade_repository=None,  # type: ignore[arg-type]
        cm_brigade_repository=None,  # type: ignore[arg-type]
        cm_brigade_error_screen_repository=None,  # type: ignore[arg-type]
        spo_repository=_Spo(),  # type: ignore[arg-type]
        mrp_inputs_service=_MrpInputs(),  # type: ignore[arg-type]
    )

    items = asyncio.run(use_case.execute(SimpleNamespace(ngdu_id=4)))  # type: ignore[arg-type]

    flags = {i.name: i.is_frequent_repair for i in items}
    # ГТМ не считается: у VMB_0002 только два ремонта оборудования
    assert flags == {"VMB_0001": True, "VMB_0002": False, "VMB_0003": False}
