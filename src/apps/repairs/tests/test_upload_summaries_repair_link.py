"""Сводке проставляется repair_id покрывающего ремонта — по нему её отдаёт API."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from apps.repairs.dto.requests.summaries import UploadParsedSummaryDTO
from apps.repairs.use_cases.upload_parsed_summaries import UploadParsedSummariesUseCase

WELL = SimpleNamespace(id=7, name="BLG_0251", abai_id=1007)


def _summary(day: date, shift: int = 1) -> UploadParsedSummaryDTO:
    return UploadParsedSummaryDTO(
        start_date=day,
        brigade_number=11,
        well_name="BLG_0251",
        second_well_name=None,
        pump_type="ШГН",
        shift_type_number=shift,
        car="Меликеев Т. 249AMD",
        device_number="15680",
        shift_details=["08:00-08:30ч. Прибытие на скважину."],
    )


def _repair(rid: int, start: datetime, end: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid, well_id=None, abai_well_id=1007, start_time=start, end_time=end,
    )


def test_repair_id_is_latest_covering_repair_or_none() -> None:
    repairs_by_well = {
        7: [
            _repair(1, datetime(2026, 9, 1), datetime(2026, 9, 10)),  # noqa: DTZ001
            _repair(2, datetime(2026, 9, 17), None),  # noqa: DTZ001
        ],
    }
    summaries = [
        _summary(date(2026, 9, 5)),
        _summary(date(2026, 9, 20)),
        _summary(date(2026, 9, 20), shift=2),
        _summary(date(2026, 9, 14)),  # между ремонтами
    ]

    dtos = UploadParsedSummariesUseCase._build_create_dtos(  # noqa: SLF001
        summaries,
        {"BLG_0251": WELL},
        repairs_by_well,
    )

    by_date = {(d.date, d.shift_type_number): d.repair_id for d in dtos.values()}
    assert by_date == {
        (date(2026, 9, 5), 1): 1,
        (date(2026, 9, 20), 1): 2,
        (date(2026, 9, 20), 2): 2,
        (date(2026, 9, 14), 1): None,
    }
