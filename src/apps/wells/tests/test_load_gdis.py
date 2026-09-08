"""Разбор строк ABAI ГДИС в DTO зеркала."""

from datetime import date
from types import SimpleNamespace

from apps.wells.repositories.gdis import MAX_QUERY_PARAMS, rows_per_statement
from apps.wells.tasks.load_gdis.load_gdis import conclusion_ids, current_dto


def test_conclusion_ids_drops_null_elements() -> None:
    assert conclusion_ids(None) is None
    assert conclusion_ids([]) is None
    assert conclusion_ids([None]) is None
    assert conclusion_ids([12, None, 7]) == [12, 7]
    assert conclusion_ids([3]) == [3]


def test_current_dto_survives_null_only_conclusion_array() -> None:
    row = SimpleNamespace(
        id=1,
        well=100,
        meas_date=date(2026, 9, 1),
        reason=None,
        reason_txt=None,
        device=None,
        target=None,
        note=None,
        transcript_dynamogram=None,
        conclusion=None,
        conclusion_arr=[None],
        conclusion_text=None,
    )

    dto = current_dto(row)

    assert dto.abai_id == 1
    assert dto.abai_well_id == 100
    assert dto.conclusion_arr is None


def test_rows_per_statement_respects_asyncpg_parameter_limit() -> None:
    for columns in (1, 5, 12, 40):
        assert rows_per_statement(columns) * columns <= MAX_QUERY_PARAMS
        assert rows_per_statement(columns) >= 1
    # 5000 исследований по 12 колонок раньше давали 60 000 параметров
    assert rows_per_statement(12) < 5000
