"""Разбор отчёта «Ревизия фонда скважин» (xlsx) без сторонних библиотек.

xlsx — zip с XML: первый лист, общие строки, числа. Отчёт с многоэтажной
шапкой (строки 6–8), номерами колонок в строке 9 и данными с 10-й. Колонки
берутся по номеру, а раскладка проверяется по заголовкам: если отчёт
поменяли, разбор падает с понятной ошибкой, а не тихо читает не то.

Используется загрузчиком ``load_fund_revision`` (сейчас — разово из файла от
28.10.2026) и будет использоваться эндпоинтом ежедневной загрузки.
"""

import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from defusedxml.ElementTree import fromstring  # файл приходит извне — без XXE

from shared.errors import AppError

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

type CellValue = str | float | bool | None

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_EXCEL_EPOCH = date(1899, 12, 30)

FIRST_DATA_ROW = 10

# Колонки (с нуля, A = 0).
COL_NGDU, COL_CDNG, COL_WELL = 1, 2, 4
COL_PLAN_OIL, COL_FACT_LIQ, COL_FACT_WC, COL_FACT_OIL = 29, 30, 31, 32
COL_LIFT = 36
COL_DEPTH = {"ШГН": 42, "ЭВН": 48, "УЭЦН": 51}
COL_GDIS_DATE, COL_HDIN = 66, 68
COL_PREV_PRS, COL_LAST_PRS = 71, 72
EXTRA_COLUMNS = {
    "sk_type": 37,
    "stroke_m": 38,
    "strokes_per_min": 39,
    "shgn_pump_code": 40,
    "shgn_diameter_mm": 41,
    "evn_drive_type": 43,
    "evn_drive_pulley_mm": 44,
    "evn_motor_pulley_mm": 45,
    "evn_motor_rpm": 46,
    "evn_diameter_mm": 47,
    "esp_pump_code": 49,
    "esp_diameter_mm": 50,
    "gdis_result": 67,
    "p_bottom_atm": 69,
}

# Заголовки, по которым проверяется раскладка: (строка, колонка, текст).
EXPECTED_HEADERS = (
    (6, COL_WELL, "Скважина"),
    (6, COL_LIFT, "Способ эксплуатации"),
    (6, COL_GDIS_DATE, "Последний ГДИС"),
    (6, COL_LAST_PRS, "Дата последнего ПРС"),
    (7, COL_HDIN, "Ндин, м"),
    (8, COL_DEPTH["ШГН"], "Глубина спуска ШГН, м"),
    (8, COL_DEPTH["ЭВН"], "Глубина спуска ЭВН, м"),
    (8, COL_DEPTH["УЭЦН"], "Глубина спуска ЭЦН, м"),
)


class FundRevisionFormatError(AppError):
    message = "Fund revision workbook has an unexpected layout"
    code = "fund_revision_format"


@dataclass(frozen=True, slots=True)
class FundRevisionRow:
    well_name: str
    ngdu_name: str | None
    cdng_name: str | None
    lift_type: str | None
    pump_depth_m: float | None
    hdin_m: float | None
    gdis_date: date | None
    last_prs_date: date | None
    prev_prs_date: date | None
    plan_oil: float | None
    fact_oil: float | None
    fact_liquid: float | None
    fact_water_cut: float | None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Без пустых полей — так выгрузка ревизии в репозитории вдвое меньше."""
        return {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in asdict(self).items()
            if value not in (None, {})
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "FundRevisionRow":
        values = {name: data.get(name) for name in cls.__dataclass_fields__}
        values["extra"] = values["extra"] or {}
        for key in ("gdis_date", "last_prs_date", "prev_prs_date"):
            if values[key]:
                values[key] = date.fromisoformat(values[key])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class FundRevisionSheet:
    revision_date: date
    rows: list[FundRevisionRow]


def _column_index(ref: str) -> int:
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in si.iter(f"{_NS}t"))
        for si in root.iter(f"{_NS}si")
    ]


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f"{_NS}sheets/{_NS}sheet")
    if sheet is None:
        raise FundRevisionFormatError(details={"reason": "no sheets"})
    rel_id = sheet.get(f"{_REL_NS}id")
    rels = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.iter(f"{_PKG_REL_NS}Relationship"):
        if rel.get("Id") == rel_id:
            target = rel.get("Target", "").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise FundRevisionFormatError(details={"reason": "sheet relation not found"})


def _cell_value(cell: "Element", strings: list[str]) -> CellValue:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_NS}t"))
    raw = cell.findtext(f"{_NS}v")
    if raw is None or kind in ("str", "e"):
        return raw
    if kind == "s":
        return strings[int(raw)]
    if kind == "b":
        return raw == "1"
    try:
        return float(raw)
    except ValueError:
        return raw


def read_rows(content: bytes) -> dict[int, dict[int, Any]]:
    """Строки первого листа: номер строки (с 1) → {колонка (с 0): значение}."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        strings = _shared_strings(archive)
        sheet = archive.read(_first_sheet_path(archive))
    rows: dict[int, dict[int, Any]] = {}
    for row in fromstring(sheet).iter(f"{_NS}row"):
        values = {}
        for cell in row.iter(f"{_NS}c"):
            value = _cell_value(cell, strings)
            if value not in (None, ""):
                values[_column_index(cell.get("r", ""))] = value
        rows[int(row.get("r", "0"))] = values
    return rows


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _date(value: object) -> date | None:
    """Дата ячейки: число — сериальная дата Excel, строка — ДД.ММ.ГГГГ / ISO."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _EXCEL_EPOCH + timedelta(days=int(value))
    text = str(value).strip()
    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            # Только дата, без часового пояса — как в отчёте.
            return datetime.strptime(text, pattern).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _revision_date(rows: dict[int, dict[int, Any]]) -> date:
    for number in range(1, FIRST_DATA_ROW):
        for value in rows.get(number, {}).values():
            match = re.search(r"Дата:\s*(\d{2}\.\d{2}\.\d{4})", str(value))
            if match:
                return datetime.strptime(match.group(1), "%d.%m.%Y").date()  # noqa: DTZ007
    raise FundRevisionFormatError(details={"reason": "revision date not found"})


def _check_layout(rows: dict[int, dict[int, Any]]) -> None:
    for number, column, expected in EXPECTED_HEADERS:
        actual = _text(rows.get(number, {}).get(column))
        if actual is None or not actual.startswith(expected):
            raise FundRevisionFormatError(
                details={
                    "row": number,
                    "column": column,
                    "expected": expected,
                    "actual": actual,
                },
            )


def parse_fund_revision(content: bytes) -> FundRevisionSheet:
    """Разобрать xlsx «Ревизия фонда скважин» в строки по скважинам."""
    rows = read_rows(content)
    _check_layout(rows)
    revision_date = _revision_date(rows)
    result = []
    for number in sorted(rows):
        if number < FIRST_DATA_ROW:
            continue
        values = rows[number]
        well = _text(values.get(COL_WELL))
        if well is None:
            continue
        lift = _text(values.get(COL_LIFT))
        depth_column = COL_DEPTH.get(lift or "")
        depth = _number(values.get(depth_column)) if depth_column is not None else None
        if depth is None:
            # Способ не указан или колонка пуста — первая заполненная глубина.
            depth = next(
                (
                    _number(values.get(column))
                    for column in COL_DEPTH.values()
                    if _number(values.get(column)) is not None
                ),
                None,
            )
        result.append(
            FundRevisionRow(
                well_name=well,
                ngdu_name=_text(values.get(COL_NGDU)),
                cdng_name=_text(values.get(COL_CDNG)),
                lift_type=lift,
                pump_depth_m=depth if depth and depth > 0 else None,
                hdin_m=_number(values.get(COL_HDIN)),
                gdis_date=_date(values.get(COL_GDIS_DATE)),
                last_prs_date=_date(values.get(COL_LAST_PRS)),
                prev_prs_date=_date(values.get(COL_PREV_PRS)),
                plan_oil=_number(values.get(COL_PLAN_OIL)),
                fact_oil=_number(values.get(COL_FACT_OIL)),
                fact_liquid=_number(values.get(COL_FACT_LIQ)),
                fact_water_cut=_number(values.get(COL_FACT_WC)),
                extra={
                    key: values[column]
                    for key, column in EXTRA_COLUMNS.items()
                    if values.get(column) not in (None, "")
                },
            ),
        )
    return FundRevisionSheet(revision_date=revision_date, rows=result)
