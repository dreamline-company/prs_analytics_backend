"""Разбор «Ревизии фонда скважин» (xlsx) без сторонних библиотек."""

import io
import zipfile
from datetime import date

import pytest

from apps.wells.services.fund_revision.parser import (
    COL_DEPTH,
    COL_GDIS_DATE,
    COL_HDIN,
    COL_LAST_PRS,
    COL_LIFT,
    COL_NGDU,
    COL_PREV_PRS,
    COL_WELL,
    EXPECTED_HEADERS,
    FundRevisionFormatError,
    FundRevisionRow,
    parse_fund_revision,
)

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(ord("A") + rest) + name
    return name


def _xlsx(rows: dict[int, dict[int, object]]) -> bytes:
    """Минимальный xlsx: строки — inline, числа — числами."""
    sheet = []
    for number in sorted(rows):
        cells = []
        for column, value in sorted(rows[number].items()):
            ref = f"{_col(column)}{number}"
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
        sheet.append(f'<row r="{number}">{"".join(cells)}</row>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{_NS}" xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships"><sheets>'
            '<sheet name="Отчет" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            'Type="worksheet"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{_NS}"><sheetData>{"".join(sheet)}'
            "</sheetData></worksheet>",
        )
    return buffer.getvalue()


def _headers() -> dict[int, dict[int, object]]:
    rows: dict[int, dict[int, object]] = {2: {1: "Дата: 28.10.2026"}}
    for number, column, text in EXPECTED_HEADERS:
        rows.setdefault(number, {})[column] = text
    return rows


def test_parses_rows_with_depth_by_lift_and_excel_dates() -> None:
    rows = _headers()
    rows[10] = {
        COL_NGDU: "НГДУ-Кайнармунайгаз",
        COL_WELL: "VMB_0027",
        COL_LIFT: "ЭВН",
        COL_DEPTH["ШГН"]: 999,  # чужая колонка — не берётся
        COL_DEPTH["ЭВН"]: 232,
        COL_HDIN: 55,
        COL_GDIS_DATE: 46311,  # 16.10.2026
        COL_LAST_PRS: 45575,  # 10.10.2024
        COL_PREV_PRS: "15.04.2024",
    }
    rows[11] = {COL_WELL: "UZK_0001", COL_DEPTH["ШГН"]: 800}  # способ не указан

    sheet = parse_fund_revision(_xlsx(rows))

    assert sheet.revision_date == date(2026, 10, 28)
    first, second = sheet.rows
    assert first.lift_type == "ЭВН"
    assert first.pump_depth_m == 232
    assert first.hdin_m == 55
    assert first.gdis_date == date(2026, 10, 16)
    assert first.last_prs_date == date(2024, 10, 10)
    assert first.prev_prs_date == date(2024, 4, 15)
    assert second.lift_type is None
    assert second.pump_depth_m == 800  # первая заполненная глубина


def test_round_trip_json() -> None:
    rows = _headers()
    rows[10] = {COL_WELL: "VMB_0001", COL_LIFT: "ЭВН", COL_DEPTH["ЭВН"]: 300}
    row = parse_fund_revision(_xlsx(rows)).rows[0]

    assert FundRevisionRow.from_json(row.to_json()) == row


def test_changed_layout_fails_loudly() -> None:
    rows = _headers()
    rows[8][COL_DEPTH["ЭВН"]] = "Что-то другое"

    with pytest.raises(FundRevisionFormatError):
        parse_fund_revision(_xlsx(rows))
