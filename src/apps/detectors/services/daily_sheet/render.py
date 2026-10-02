"""Рендер ведомости в docx — бланк повторяет образец технологов.

Альбомный A4, шапка, ТОП по вероятности, таблица с повторяющимся заголовком
на каждой странице, примечания и подпись. Данные приходят готовыми строками
(``DailySheetDTO``) — здесь только вёрстка.
"""

from io import BytesIO

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import _Cell

from apps.detectors.dto.internal.daily_sheet import DailySheetDTO
from apps.detectors.services.daily_sheet.config import (
    FOOTER_NOTES,
    FOOTER_SIGNATURE,
    SHEET_SUBTITLE,
    SHEET_SUBTITLES,
    SHEET_TITLE,
    TABLE_HEADERS,
    TABLE_WIDTHS_CM,
    TOP_SIZE,
)
from apps.detectors.services.daily_sheet.oil_fields import (
    OilFieldRef,
    oil_fields_label,
)

_FONT = "Times New Roman"
_BODY_PT = 10
_TABLE_PT = 8
_MARGIN_CM = 1.0


def render_docx(sheet: DailySheetDTO) -> BytesIO:
    document = Document()
    _setup_page(document)

    _paragraph(document, SHEET_TITLE, bold=True, size=14, center=True)
    _paragraph(
        document,
        SHEET_SUBTITLES.get(sheet.detector_code, SHEET_SUBTITLE),
        center=True,
    )
    rule = sheet.detector_code
    if sheet.detector_name_ru:
        rule += f" «{sheet.detector_name_ru}»"
    _paragraph(document, f"по правилу {rule}", center=True)
    scope = f"за {sheet.sheet_date:%d.%m.%Y}        НГДУ «{sheet.ngdu_name}»"
    fields_label = oil_fields_label(
        [OilFieldRef(f.id, f.prefix, f.name) for f in sheet.oil_fields],
    )
    if fields_label:
        scope += f", {fields_label}"
    _paragraph(document, scope, bold=True, center=True)
    _paragraph(document, _coverage_line(sheet), size=9)

    if sheet.rows:
        _paragraph(document, f"ТОП-{TOP_SIZE} по вероятности неисправности", bold=True)
        for item in sheet.top:
            _paragraph(document, f"{item.rank}. {item.text}")
        if sheet.attention:
            _paragraph(
                document,
                "Требуют внимания вне ТОП: " + "; ".join(sheet.attention) + ".",
            )
        _table(document, sheet)
    else:
        _paragraph(
            document,
            f"Отклонений по правилу {sheet.detector_code} за "
            f"{sheet.sheet_date:%d.%m.%Y} не зафиксировано.",
            bold=True,
        )

    if sheet.measure_requests:
        _paragraph(document, "Запросить замер (замер устарел)", bold=True)
        for line in sheet.measure_requests:
            _paragraph(document, line, size=9)

    for note in (*sheet.notes, *FOOTER_NOTES):
        _paragraph(document, note, size=8)
    _paragraph(document, FOOTER_SIGNATURE, size=9)

    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer


def _coverage_line(sheet: DailySheetDTO) -> str:
    coverage = sheet.coverage
    if coverage is None:
        return "Охват: нет данных."
    if coverage.source == "cits":
        return _cits_coverage_line(sheet)
    scope = "станций выбранных месторождений" if sheet.oil_fields else "станций НГДУ"
    text = (
        f"Охват: телеметрию за сутки дали {coverage.stations_reporting} из "
        f"{coverage.stations_total} {scope}"
    )
    if coverage.stations_processed is not None:
        text += f"; правило обработало {coverage.stations_processed}"
    text += "."
    if coverage.partial_day:
        text += " Сутки не завершены — данные неполные, ведомость предварительная."
    return text


def _cits_coverage_line(sheet: DailySheetDTO) -> str:
    coverage = sheet.coverage
    scope = "выбранных месторождений" if sheet.oil_fields else "НГДУ"
    text = (
        f"Охват: замеры ЦИТС за сутки есть у {coverage.stations_reporting} из "
        f"{coverage.stations_total} скважин {scope} с замерами за окно правила"
    )
    if coverage.stations_processed is not None:
        text += (
            "; сутки правилом обработаны"
            if coverage.stations_processed
            else "; сутки правилом ещё не обработаны"
        )
    text += "."
    if coverage.partial_day:
        text += " Сутки не завершены — данные неполные, ведомость предварительная."
    return text


def _setup_page(document: Document) -> None:
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    width, height = section.page_width, section.page_height
    if width < height:
        section.page_width, section.page_height = height, width
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(_MARGIN_CM))
    normal = document.styles["Normal"]
    normal.font.name = _FONT
    normal.font.size = Pt(_BODY_PT)
    # Кириллица в Word берёт шрифт из eastAsia-атрибута, иначе подставит Calibri.
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT)
    normal.paragraph_format.space_after = Pt(3)


def _paragraph(
    document: Document,
    text: str,
    *,
    bold: bool = False,
    size: int | None = None,
    center: bool = False,
) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = bold
    if size is not None:
        run.font.size = Pt(size)
    if center:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _table(document: Document, sheet: DailySheetDTO) -> None:
    table = document.add_table(rows=1, cols=len(TABLE_HEADERS))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    header = table.rows[0]
    _repeat_header(header)
    for cell, title in zip(header.cells, TABLE_HEADERS, strict=True):
        _fill(cell, title, bold=True)

    for row in sheet.rows:
        cells = table.add_row().cells
        values = (
            str(row.number),
            row.well_name,
            row.category or "—",
            f"{row.detected_at:%d.%m.%Y, %H:%M}",
            row.status_label,
            row.deviation,
            row.cause,
            str(row.probability_percent),
            row.rates,
            row.plan_oil,
        )
        for cell, value in zip(cells, values, strict=True):
            _fill(cell, value)

    for table_row in table.rows:
        for cell, width in zip(table_row.cells, TABLE_WIDTHS_CM, strict=True):
            cell.width = Cm(width)


def _fill(cell: _Cell, text: str, *, bold: bool = False) -> None:
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(_TABLE_PT)


def _repeat_header(row) -> None:  # noqa: ANN001 — docx._Row без публичного типа
    properties = row._tr.get_or_add_trPr()  # noqa: SLF001
    element = OxmlElement("w:tblHeader")
    element.set(qn("w:val"), "true")
    properties.append(element)
