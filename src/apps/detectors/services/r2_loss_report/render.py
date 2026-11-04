"""xlsx отчёта «Обрыв штанг» в раскладке образца — без сторонних библиотек.

xlsx — zip с XML (как и в разборе ревизии фонда). Лист один: в A1:A2 и B1:B2
(объединены) итог недобора, в строке 4 шапка, с 5-й — скважины. Стили образца:
Arial, серая шапка с рамкой и переносом, недобор жидкости в процентах, недобор
нефти жирным с одним знаком, дата сигнала ДД.ММ.ГГГГ.
"""

import io
import math
import zipfile
from collections.abc import Sequence
from datetime import date
from xml.sax.saxutils import escape

from apps.detectors.dto.internal.r2_loss_report import R2LossReportDTO, R2LossRowDTO
from apps.detectors.services.r2_loss_report import config

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_EXCEL_EPOCH = date(1899, 12, 30)

HEADER_ROW = 4
FIRST_DATA_ROW = 5
_DEFAULT_WIDTH = 8.43
_LINE_HEIGHT = 12.8
_MIN_ROW_HEIGHT = 30.0

# Индексы cellXfs из _STYLES.
_S_TITLE, _S_TITLE_NUMBER, _S_HEADER, _S_CELL, _S_PERCENT, _S_LOSS, _S_DATE = range(
    1,
    8,
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\
<Default Extension="xml" ContentType="application/xml"/>\
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\
</Types>"""  # noqa: E501

_ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_PKG_REL_NS}">\
<Relationship Id="rId1" Type="{_REL_NS}/officeDocument" Target="xl/workbook.xml"/>\
</Relationships>"""

_WORKBOOK = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">\
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"""

_WORKBOOK_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_PKG_REL_NS}">\
<Relationship Id="rId1" Type="{_REL_NS}/worksheet" Target="worksheets/sheet1.xml"/>\
<Relationship Id="rId2" Type="{_REL_NS}/styles" Target="styles.xml"/>\
</Relationships>"""

_CENTER = '<alignment horizontal="center" vertical="center" wrapText="1"/>'
_BOXED = 'borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"'
_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{_MAIN_NS}">\
<numFmts count="2"><numFmt numFmtId="164" formatCode="0.0"/>\
<numFmt numFmtId="165" formatCode="dd\\.mm\\.yyyy"/></numFmts>\
<fonts count="4">\
<font><sz val="10"/><name val="Arial"/><family val="2"/></font>\
<font><b/><sz val="12"/><name val="Arial"/><family val="2"/></font>\
<font><b/><sz val="11"/><name val="Arial"/><family val="2"/></font>\
<font><sz val="11"/><name val="Arial"/><family val="2"/></font>\
</fonts>\
<fills count="3"><fill><patternFill patternType="none"/></fill>\
<fill><patternFill patternType="gray125"/></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FFE5E5E5"/>\
<bgColor indexed="64"/></patternFill></fill></fills>\
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>\
<border><left style="thin"><color auto="1"/></left>\
<right style="thin"><color auto="1"/></right>\
<top style="thin"><color auto="1"/></top>\
<bottom style="thin"><color auto="1"/></bottom><diagonal/></border></borders>\
<cellStyleXfs count="1">\
<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>\
<cellXfs count="8">\
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>\
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>\
<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" \
applyNumberFormat="1"/>\
<xf numFmtId="0" fontId="2" fillId="2" {_BOXED} applyFill="1">{_CENTER}</xf>\
<xf numFmtId="0" fontId="3" fillId="0" {_BOXED}>{_CENTER}</xf>\
<xf numFmtId="9" fontId="3" fillId="0" {_BOXED} applyNumberFormat="1">{_CENTER}</xf>\
<xf numFmtId="164" fontId="2" fillId="0" {_BOXED} applyNumberFormat="1">{_CENTER}</xf>\
<xf numFmtId="165" fontId="3" fillId="0" {_BOXED} applyNumberFormat="1">{_CENTER}</xf>\
</cellXfs>\
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>\
</styleSheet>"""

type CellValue = str | float | int | date | None


def file_name(report_date: date) -> str:
    return f"Otchet_Obryv_Shtang_{report_date:%Y-%m-%d}.xlsx"


def headers(report_date: date) -> list[str]:
    return [title.format(date=f"{report_date:%d.%m}") for title in config.HEADERS]


def row_values(row: R2LossRowDTO) -> list[tuple[CellValue, int]]:
    """Значения колонок A–O строки со стилями образца."""
    return [
        (row.cdng, _S_CELL),
        (row.well_name, _S_CELL),
        (row.category, _S_CELL),
        (row.plan_water_cut, _S_CELL),
        (row.plan_oil, _S_CELL),
        (row.plan_liquid, _S_CELL),
        (row.liquid_after, _S_CELL),
        (row.liquid_loss_share, _S_PERCENT),
        (row.oil_loss, _S_LOSS),
        (row.water_cut_before, _S_CELL),
        (row.oil_before, _S_CELL),
        (row.what_happens, _S_CELL),
        (row.signal_date, _S_DATE),
        (row.abai_status, _S_CELL),
        (row.idle_days, _S_CELL),
    ]


def render_xlsx(report: R2LossReportDTO) -> io.BytesIO:
    sheet_rows = [
        _row_xml(
            1,
            [(config.TITLE, _S_TITLE), (report.total_oil_loss, _S_TITLE_NUMBER)],
            height=_LINE_HEIGHT,
        ),
    ]
    header_cells = [(title, _S_HEADER) for title in headers(report.report_date)]
    sheet_rows.append(
        _row_xml(HEADER_ROW, header_cells, height=_estimate_height(header_cells)),
    )
    for offset, row in enumerate(report.rows):
        cells = row_values(row)
        sheet_rows.append(
            _row_xml(FIRST_DATA_ROW + offset, cells, height=_estimate_height(cells)),
        )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">'
        f'<sheetFormatPr defaultRowHeight="{_LINE_HEIGHT}"/>'
        f"<cols>{_cols_xml()}</cols>"
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        '<mergeCells count="2"><mergeCell ref="A1:A2"/><mergeCell ref="B1:B2"/>'
        "</mergeCells>"
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" '
        'header="0.3" footer="0.3"/>'
        "</worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    buffer.seek(0)
    return buffer


def column_letter(index: int) -> str:
    """0 → A, 14 → O, 26 → AA."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _cols_xml() -> str:
    return "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate(config.COLUMN_WIDTHS, start=1)
        if width is not None
    )


def _row_xml(
    number: int,
    cells: Sequence[tuple[CellValue, int]],
    *,
    height: float,
) -> str:
    body = "".join(
        _cell_xml(f"{column_letter(i)}{number}", value, style)
        for i, (value, style) in enumerate(cells)
    )
    return f'<row r="{number}" ht="{height:.1f}" customHeight="1">{body}</row>'


def _cell_xml(ref: str, value: CellValue, style: int) -> str:
    if value is None:
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, date):
        return f'<c r="{ref}" s="{style}"><v>{(value - _EXCEL_EPOCH).days}</v></c>'
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int | float):
        return f'<c r="{ref}" s="{style}"><v>{value!r}</v></c>'
    text = escape(str(value))
    return (
        f'<c r="{ref}" s="{style}" t="inlineStr">'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


def _estimate_height(cells: Sequence[tuple[CellValue, int]]) -> float:
    """Высота строки под самый длинный перенос: Excel по открытию её не считает."""
    lines = 1
    for (value, _), width in zip(cells, config.COLUMN_WIDTHS, strict=False):
        if not isinstance(value, str):
            continue
        per_line = max(1, int(width or _DEFAULT_WIDTH))
        lines = max(lines, math.ceil(len(value) / per_line))
    return max(_MIN_ROW_HEIGHT, lines * _LINE_HEIGHT + 4)
