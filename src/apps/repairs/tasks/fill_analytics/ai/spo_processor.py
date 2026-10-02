"""LangGraph processor for a single SPO measurement."""

import math
from dataclasses import dataclass
from typing import Any

from langchain.messages import HumanMessage

from apps.wells.models.spo import SPO

from .base import BaseAIProcessor


@dataclass(slots=True)
class SPOProcessingInput:
    spo: SPO
    raw_s3_key: str
    chart_s3_key: str | None
    notes_s3_key: str | None
    repair_id: int
    chart_text: str | None = None
    notes_text: str | None = None


# Модель с контекстом 16 384 токена считает примерно токен на символ, строка
# сжатого графика — ~35 токенов: длинный замер целиком не влезал, и разбор
# падал с 400. 200 строк — ~7 тыс. токенов, остальное — на ответ.
MAX_CHART_ROWS = 200
COMPACT_HEADER = (
    "datetime,hook_weight_t_min,hook_weight_t_max,h2s_mg_m3_max,ch4_percent_max"
)
# Колонки chart.csv из spo_persist._render_csv.
_CHART_COLUMNS = {"datetime", "hook_weight_t", "h2s_mg_m3", "ch4_percent"}
# Газы в chart.csv: колонка, имя для модели, единица.
_GASES = (("h2s_mg_m3", "H2S", "mg/m3"), ("ch4_percent", "CH4", "%"))


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def compact_chart_csv(text: str, *, max_rows: int = MAX_CHART_ROWS) -> str:
    """График СПО не длиннее ``max_rows`` строк.

    Короткий возвращается как есть. Длинный режется на подряд идущие
    интервалы: время начала, min/max веса на крюке (пики и провалы нагрузки)
    и max газов — усреднение их бы сгладило.
    """
    lines = text.strip().splitlines()
    if len(lines) - 1 <= max_rows:
        return text
    columns = {name: i for i, name in enumerate(lines[0].split(","))}
    if not columns.keys() >= _CHART_COLUMNS:
        return text
    rows = [row for line in lines[1:] if len(row := line.split(",")) == len(columns)]
    step = max(1, math.ceil(len(rows) / max_rows))

    def values(chunk: list[list[str]], name: str) -> list[float]:
        i = columns[name]
        return [v for row in chunk if (v := _number(row[i])) is not None]

    out = [COMPACT_HEADER]
    for start in range(0, len(rows), step):
        chunk = rows[start : start + step]
        weight = values(chunk, "hook_weight_t")
        h2s = values(chunk, "h2s_mg_m3")
        ch4 = values(chunk, "ch4_percent")
        out.append(
            ",".join(
                [
                    chunk[0][columns["datetime"]],
                    _fmt(min(weight, default=None)),
                    _fmt(max(weight, default=None)),
                    _fmt(max(h2s, default=None)),
                    _fmt(max(ch4, default=None)),
                ],
            ),
        )
    return "\n".join(out)


def drop_empty_columns(text: str) -> str:
    """CSV без колонок, пустых во всех строках.

    Без газоанализатора строка кончается на ``,,``, и модель читала максимум
    веса на крюке как H2S («выброс 10.331 мг/м3»): нет колонки — нечего путать.
    """
    rows = [line.split(",") for line in text.strip().splitlines()]
    if len(rows) < 2:  # noqa: PLR2004 — заголовок и хотя бы одна строка
        return text
    keep = [
        i
        for i in range(len(rows[0]))
        if any(i < len(row) and row[i] for row in rows[1:])
    ]
    if len(keep) == len(rows[0]):
        return text
    return "\n".join(
        ",".join(row[i] if i < len(row) else "" for i in keep) for row in rows
    )


def gas_summary(text: str) -> str:
    """Пики газов за весь замер, посчитанные кодом, а не найденные моделью."""
    lines = text.strip().splitlines()
    columns = {name: i for i, name in enumerate(lines[0].split(","))}
    parts = []
    for column, label, unit in _GASES:
        peak: tuple[float, str] | None = None
        if column in columns:
            for line in lines[1:]:
                row = line.split(",")
                value = _number(row[columns[column]])
                if value is not None and (peak is None or value > peak[0]):
                    peak = (value, row[columns["datetime"]])
        parts.append(
            f"{label}: no sensor data in this measurement"
            if peak is None
            else f"{label} max {peak[0]:g} {unit} at {peak[1]}",
        )
    return "; ".join(parts)


class SPOAIProcessor(BaseAIProcessor[SPOProcessingInput]):
    # Не поднята при сжатии графика: короткие замеры идут в модель как раньше,
    # а длинные раньше не доходили до ответа — пересчитывать готовые незачем.
    # Не поднята и при сводке газов: разборы, где модель приняла вес за газ,
    # перезапущены точечно, остальные не трогаем.
    prompt_version: str = "v1"

    def _build_state(self, item: SPOProcessingInput) -> dict[str, Any]:
        parts: list[str] = [
            f"Analyze SPO measurement (spo_id={item.spo.id}, "
            f"snapshot_time={item.spo.snapshot_time.isoformat()}). "
            "Chart CSV and notes JSON are attached below. Return findings as JSON.",
        ]
        if item.chart_text is not None:
            chart = compact_chart_csv(item.chart_text)
            if chart is not item.chart_text:
                parts.append(
                    "chart.csv is compacted to fit the model: consecutive points "
                    "are grouped into intervals (min/max hook weight, max gas).",
                )
            parts.append(
                "Gas sensors, computed from the full measurement: "
                f"{gas_summary(item.chart_text)}. Report gas only from this "
                "line; other chart columns are not gas.",
            )
            parts.append(f"chart.csv:\n```csv\n{drop_empty_columns(chart)}\n```")
        if item.notes_text is not None:
            parts.append(f"notes.json:\n```json\n{item.notes_text}\n```")
        return {"messages": [HumanMessage(content="\n\n".join(parts))]}
