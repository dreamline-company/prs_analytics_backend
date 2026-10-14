"""МРП скважины через use case: ремонты из репозитория -> DTO ответа."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.repairs.dto.queries.mrp import GetWellMrpQuery
from apps.repairs.use_cases.get_well_mrp import GetWellMrpUseCase, WellNotFoundError

NOW = datetime(2026, 10, 2, 12, 42)  # noqa: DTZ001


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _repair(
    repair_id: int,
    start: str,
    end: str | None,
    text: str = "Обрыв штанги",
    type_id: int = 3020,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=repair_id,
        abai_well_id=1001,
        repair_type_id=type_id,
        work_list=text,
        start_time=_dt(start),
        end_time=_dt(end) if end else None,
    )


class FakeRepairRepository:
    def __init__(self, rows: list[tuple[SimpleNamespace, str | None]]) -> None:
        self.rows = rows

    async def list_with_type_by_well_abai_ids(
        self,
        abai_well_ids: list[int],
        *,
        since: datetime | None = None,  # noqa: ARG002
    ) -> list[tuple[SimpleNamespace, str | None]]:
        assert abai_well_ids == [1001]
        return self.rows


class FakeWellRepository:
    async def get_by_id(self, id_: int) -> SimpleNamespace | None:
        if id_ != 1:
            return None
        return SimpleNamespace(id=1, abai_id=1001, name="VMB_0418")


def _use_case(rows: list[tuple[SimpleNamespace, str | None]]) -> GetWellMrpUseCase:
    return GetWellMrpUseCase(
        repair_repository=FakeRepairRepository(rows),  # type: ignore[arg-type]
        well_repository=FakeWellRepository(),  # type: ignore[arg-type]
    )


ROWS = [
    (_repair(1, "2026-03-10T00:00", "2026-03-12T00:00"), "Обрыв штанг"),
    (
        _repair(2, "2026-06-01T00:00", "2026-06-02T00:00", "По мероприятию", 3012),
        "По мероприятию",
    ),
    (_repair(3, "2026-08-05T00:00", "2026-08-06T00:00"), "Обрыв штанг"),
]


def test_response_has_per_repair_mrp_newest_first() -> None:
    dto = asyncio.run(
        _use_case(ROWS).execute(GetWellMrpQuery(well_id=1), now=NOW),
    )

    assert dto.well_name == "VMB_0418"
    assert [r.repair_id for r in dto.repairs] == [3, 2, 1]
    assert dto.intervals_count == 2
    assert dto.avg_mrp_days == pytest.approx((81 + 64) / 2, abs=0.1)
    last = dto.repairs[0]
    assert last.mrp_before_days == pytest.approx(64.0)
    assert last.running_days == pytest.approx(57.5, abs=0.1)  # 06.08 → 02.10 12:42
    assert last.verdict == "watching"


def test_gtm_flag_comes_from_repair_type() -> None:
    dto = asyncio.run(
        _use_case(ROWS).execute(GetWellMrpQuery(well_id=1), now=NOW),
    )

    by_id = {r.repair_id: r for r in dto.repairs}
    assert by_id[2].is_gtm
    assert by_id[2].repair_type_name_ru == "По мероприятию"
    assert not by_id[1].is_gtm


def test_focus_repair_adds_its_numbers_to_interpretation() -> None:
    dto = asyncio.run(
        _use_case(ROWS).execute(
            GetWellMrpQuery(well_id=1),
            now=NOW,
            focus_repair_id=2,
        ),
    )

    assert "Текущий ремонт" in dto.interpretation


def test_unknown_well_is_404() -> None:
    with pytest.raises(WellNotFoundError) as error:
        asyncio.run(
            _use_case(ROWS).execute(GetWellMrpQuery(well_id=999), now=NOW),
        )

    assert error.value.status_code == 404
