"""Сводка привязывает ремонт к бригаде НГДУ его скважины, а не к одноимённой
бригаде другого НГДУ."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from apps.repairs.dto.requests.summaries import UploadParsedSummaryDTO
from apps.repairs.use_cases.upload_parsed_summaries import UploadParsedSummariesUseCase

DAY = date(2026, 9, 29)
WELLS = {
    "ZHT_0097": SimpleNamespace(id=1, name="ZHT_0097", abai_id=101),  # Жайык
    "VMB_1112": SimpleNamespace(id=2, name="VMB_1112", abai_id=102),  # Кайнар
}
NGDU_BY_ABAI_WELL = {101: 4, 102: 5}
BRIGADES = [
    SimpleNamespace(id=13, name="Бригада №13", ngdu_id=5),  # Кайнар
    SimpleNamespace(id=213, name="Бригада №13", ngdu_id=4),  # Жайык
]


def _summary(well: str) -> UploadParsedSummaryDTO:
    return UploadParsedSummaryDTO(
        start_date=DAY,
        brigade_number=13,
        well_name=well,
        second_well_name=None,
        pump_type="ШГН",
        shift_type_number=1,
        car="Меликеев Т. 249AMD",
        device_number="15680",
        shift_details=["08:00-08:30ч. Прибытие на скважину."],
    )


class _Links:
    def __init__(self) -> None:
        self.created: dict[int, int] = {}

    async def list_by_repair_ids(self, _ids: list[int]) -> list:
        return []

    async def create(self, dto: object) -> None:
        self.created[dto.repair_id] = dto.brigade_id


class _Brigades:
    async def list_by_names(self, names: list[str]) -> list:
        return [b for b in BRIGADES if b.name in names]


class _Ngdu:
    async def execute(self, abai_well_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=NGDU_BY_ABAI_WELL[abai_well_id])


def test_brigade_is_taken_from_well_ngdu() -> None:
    links = _Links()
    use_case = UploadParsedSummariesUseCase(
        session=None,  # type: ignore[arg-type]
        well_repository=None,  # type: ignore[arg-type]
        repair_summary_repository=None,  # type: ignore[arg-type]
        repair_repository=None,  # type: ignore[arg-type]
        repair_brigade_repository=links,  # type: ignore[arg-type]
        unique_brigade_repository=_Brigades(),  # type: ignore[arg-type]
        get_ngdu_for_well=_Ngdu(),  # type: ignore[arg-type]
    )
    start = datetime(2026, 9, 27)  # noqa: DTZ001
    repairs = {
        1: [SimpleNamespace(id=501, start_time=start, end_time=None)],
        2: [SimpleNamespace(id=502, start_time=start, end_time=None)],
    }

    linked = asyncio.run(
        use_case._link_brigades(  # noqa: SLF001
            [_summary("ZHT_0097"), _summary("VMB_1112")],
            WELLS,  # type: ignore[arg-type]
            repairs,  # type: ignore[arg-type]
        ),
    )

    assert linked == 2
    assert links.created == {501: 213, 502: 13}
