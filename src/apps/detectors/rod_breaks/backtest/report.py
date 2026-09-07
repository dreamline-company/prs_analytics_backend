"""Рендер отчёта: HTML-страница на скважину + сводный index.html.

SVG рисуется руками (без сторонних либ), стиль — как в
``src/draft_rod_break.py``: 1800×920, две панели (момент + скорость),
подложка — вертикальные полосы ремонтов, полилиния — mom_min/spd,
пунктир — трейлинг-порог 0.4×med24, маркеры сверху — эпизоды.
"""

# ruff: noqa: E501 — файл целиком про рендер HTML/SVG, длинные f-string разбивать вредно.

from __future__ import annotations

from datetime import timedelta
from html import escape
from typing import TYPE_CHECKING

from apps.detectors.rod_breaks import config

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime
    from pathlib import Path

    from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h

    from .runner import Episode, RepairSpan, WellResult

# --- SVG-геометрия (стиль draft_rod_break.py) ---
_SVG_WIDTH = 1800
_SVG_HEIGHT = 920
_PLOT_LEFT = 105
_PLOT_RIGHT = 1765
_PLOT_WIDTH = _PLOT_RIGHT - _PLOT_LEFT
_PANEL_TOPS = (110, 500)
_PANEL_HEIGHT = 315
_MARKER_STRIP_HEIGHT = 12  # полоска над панелью для маркеров эпизодов
_Y_TICKS = 5
_X_TICKS = 10
_MIN_POLYLINE_POINTS = 2  # <polyline> с 1 точкой не рендерится

# --- Цвета ---
_COL_MOMENT = "#1f4e79"
_COL_SPEED = "#2e7d32"
_COL_THRESHOLD = "#9e9e9e"
_COL_BASE_MOMENT = "#7b1fa2"
_COL_ROD_BREAK_REPAIR = "rgba(30,120,200,0.18)"
_COL_OTHER_REPAIR = "rgba(140,140,140,0.10)"
_COL_MISSED = "#c62828"
_EPISODE_COLORS = {
    None: "#2e7d32",  # TP
    "transient": "#f57c00",
    "other_repair": "#fbc02d",
    "no_repair": "#c62828",
}
_EPISODE_LABELS = {
    None: "TP",
    "transient": "FP transient",
    "other_repair": "FP other repair",
    "no_repair": "FP no repair",
}


# ============================================================
# Sluice helpers
# ============================================================


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "—"


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "—"


def _fmt_num(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def _fmt_recall(result: WellResult) -> str:
    """Recall с дробью ремонтов, чтобы не путать с TP-эпизодами: ``90% (9/10)``."""
    total = len(result.rod_break_repairs_in_coverage)
    if not total:
        return "—"
    detected = total - len(result.missed_repair_ids)
    return f"{_fmt_pct(result.recall)} ({detected}/{total})"


def _shorten(text: str, limit: int = 160) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ============================================================
# SVG
# ============================================================


def _time_x(ts: datetime, t0: datetime, span_s: float) -> float:
    delta = (ts - t0).total_seconds()
    return _PLOT_LEFT + (delta / span_s) * _PLOT_WIDTH if span_s > 0 else _PLOT_LEFT


def _panel_y(value: float, y_top: float, y_range: tuple[float, float]) -> float:
    lo, hi = y_range
    if hi <= lo:
        return y_top + _PANEL_HEIGHT
    return y_top + _PANEL_HEIGHT - (value - lo) / (hi - lo) * _PANEL_HEIGHT


def _polyline_from(
    points: Iterable[tuple[float, float | None]],
    y_top: float,
    y_range: tuple[float, float],
    color: str,
    *,
    dashed: bool = False,
) -> str:
    """Ломаная с разрывами по None: рисуется несколькими <polyline>."""
    segments: list[list[str]] = [[]]
    for x, value in points:
        if value is None:
            if segments[-1]:
                segments.append([])
            continue
        y = _panel_y(value, y_top, y_range)
        segments[-1].append(f"{x:.1f},{y:.1f}")

    dash_attr = ' stroke-dasharray="6 4"' if dashed else ""
    parts = []
    for seg in segments:
        if len(seg) < _MIN_POLYLINE_POINTS:
            continue
        pts = " ".join(seg)
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.2"'
            f'{dash_attr} points="{pts}"/>',
        )
    return "\n".join(parts)


def _y_range_moment(
    series: Sequence[Bucket2h],
    base_moment: float | None,
) -> tuple[float, float]:
    values = [b.mom_min for b in series]
    if base_moment:
        values.append(base_moment * 1.4)
    hi = max(values) if values else 1.0
    hi = max(hi, 1.0)
    return 0.0, hi * 1.05


def _y_range_speed(series: Sequence[Bucket2h]) -> tuple[float, float]:
    values = [b.spd for b in series] or [1.0]
    return 0.0, max(values) * 1.05


def _y_ticks(y_range: tuple[float, float], y_top: float) -> str:
    lo, hi = y_range
    if hi <= lo:
        return ""
    step = (hi - lo) / _Y_TICKS
    lines = []
    for i in range(_Y_TICKS + 1):
        val = lo + step * i
        y = _panel_y(val, y_top, y_range)
        lines.append(
            f'<line x1="{_PLOT_LEFT}" y1="{y:.1f}" x2="{_PLOT_RIGHT}" y2="{y:.1f}" '
            f'stroke="#eee" stroke-width="1"/>'
            f'<text x="{_PLOT_LEFT - 6:.0f}" y="{y + 3:.1f}" font-size="10" '
            f'text-anchor="end" fill="#666">{val:.0f}</text>',
        )
    return "\n".join(lines)


def _x_ticks(t0: datetime, t1: datetime, y_bottom: float) -> str:
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return ""
    lines = []
    for i in range(_X_TICKS + 1):
        frac = i / _X_TICKS
        ts = t0 + timedelta(seconds=span * frac)
        x = _PLOT_LEFT + frac * _PLOT_WIDTH
        lines.append(
            f'<line x1="{x:.1f}" y1="{y_bottom:.1f}" x2="{x:.1f}" y2="{y_bottom + 4:.1f}" '
            f'stroke="#666" stroke-width="1"/>'
            f'<text x="{x:.1f}" y="{y_bottom + 16:.1f}" font-size="10" '
            f'text-anchor="middle" fill="#666">{ts.strftime("%Y-%m-%d")}</text>',
        )
    return "\n".join(lines)


def _panel_frame(y_top: float, label: str) -> str:
    return (
        f'<rect x="{_PLOT_LEFT}" y="{y_top}" width="{_PLOT_WIDTH}" '
        f'height="{_PANEL_HEIGHT}" fill="white" stroke="#333" stroke-width="1"/>'
        f'<text x="{_PLOT_LEFT + 6}" y="{y_top + 14}" font-size="11" '
        f'font-weight="bold" fill="#333">{escape(label)}</text>'
    )


def _repair_bands(
    repairs: Sequence[RepairSpan],
    t0: datetime,
    span_s: float,
    y_top: float,
    color: str,
) -> str:
    if span_s <= 0:
        return ""
    parts = []
    for r in repairs:
        x0 = _time_x(max(r.start_time, t0), t0, span_s)
        x1 = _time_x(min(r.end_time, t0 + timedelta(seconds=span_s)), t0, span_s)
        if x1 <= x0:
            x1 = x0 + 1
        parts.append(
            f'<rect x="{x0:.1f}" y="{y_top}" width="{x1 - x0:.1f}" '
            f'height="{_PANEL_HEIGHT}" fill="{color}"/>',
        )
    return "\n".join(parts)


def _episode_markers(
    episodes: Sequence[Episode],
    t0: datetime,
    span_s: float,
    y_top: float,
) -> str:
    if span_s <= 0:
        return ""
    parts = []
    strip_y = y_top - _MARKER_STRIP_HEIGHT
    for ep in episodes:
        x = _time_x(ep.fired_at, t0, span_s)
        color = _EPISODE_COLORS[ep.fp_class]
        parts.append(
            f'<line x1="{x:.1f}" y1="{y_top}" x2="{x:.1f}" y2="{y_top + _PANEL_HEIGHT}" '
            f'stroke="{color}" stroke-width="1" stroke-opacity="0.35"/>'
            f'<circle cx="{x:.1f}" cy="{strip_y + 6:.1f}" r="4" fill="{color}"/>',
        )
    return "\n".join(parts)


def _missed_markers(
    missed_repair_ids: Sequence[int],
    rod_break_repairs: Sequence[RepairSpan],
    t0: datetime,
    span_s: float,
    y_top: float,
) -> str:
    if span_s <= 0:
        return ""
    by_id = {r.id: r for r in rod_break_repairs}
    parts = []
    for rid in missed_repair_ids:
        r = by_id.get(rid)
        if not r:
            continue
        x = _time_x(r.start_time, t0, span_s)
        y = y_top + _PANEL_HEIGHT - 10
        d = 8
        parts.append(
            f'<line x1="{x - d}" y1="{y - d}" x2="{x + d}" y2="{y + d}" '
            f'stroke="{_COL_MISSED}" stroke-width="2"/>'
            f'<line x1="{x - d}" y1="{y + d}" x2="{x + d}" y2="{y - d}" '
            f'stroke="{_COL_MISSED}" stroke-width="2"/>',
        )
    return "\n".join(parts)


def _threshold_series(
    series: Sequence[Bucket2h],
    key: str,
    ratio: float,
) -> list[tuple[datetime, float | None]]:
    return [
        (
            b.start_ts,
            ratio * getattr(b, key) if getattr(b, key) is not None else None,
        )
        for b in series
    ]


def _render_svg(result: WellResult) -> str:
    if not result.series:
        return f'<svg width="{_SVG_WIDTH}" height="60"><text x="10" y="30">No data</text></svg>'

    t0 = result.series[0].start_ts
    t1 = result.series[-1].start_ts
    span_s = (t1 - t0).total_seconds() or 1.0
    mom_range = _y_range_moment(result.series, result.base_moment)
    spd_range = _y_range_speed(result.series)

    mom_line = _polyline_from(
        [(_time_x(b.start_ts, t0, span_s), b.mom_min) for b in result.series],
        _PANEL_TOPS[0],
        mom_range,
        _COL_MOMENT,
    )
    mom_thr = _polyline_from(
        [
            (_time_x(ts, t0, span_s), value)
            for ts, value in _threshold_series(
                result.series,
                "mom_med24",
                config.MOM_DROP_RATIO,
            )
        ],
        _PANEL_TOPS[0],
        mom_range,
        _COL_THRESHOLD,
        dashed=True,
    )
    base_line = ""
    if result.base_moment is not None:
        y = _panel_y(result.base_moment, _PANEL_TOPS[0], mom_range)
        base_line = (
            f'<line x1="{_PLOT_LEFT}" y1="{y:.1f}" x2="{_PLOT_RIGHT}" y2="{y:.1f}" '
            f'stroke="{_COL_BASE_MOMENT}" stroke-width="1" stroke-dasharray="2 3"/>'
            f'<text x="{_PLOT_RIGHT + 4}" y="{y + 3:.1f}" font-size="10" '
            f'fill="{_COL_BASE_MOMENT}">base={result.base_moment:.0f}</text>'
        )

    spd_line = _polyline_from(
        [(_time_x(b.start_ts, t0, span_s), b.spd) for b in result.series],
        _PANEL_TOPS[1],
        spd_range,
        _COL_SPEED,
    )
    spd_thr = _polyline_from(
        [
            (_time_x(ts, t0, span_s), value)
            for ts, value in _threshold_series(
                result.series,
                "spd_med24",
                config.SPD_KEEP_RATIO,
            )
        ],
        _PANEL_TOPS[1],
        spd_range,
        _COL_THRESHOLD,
        dashed=True,
    )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SVG_WIDTH}" '
        f'height="{_SVG_HEIGHT}" viewBox="0 0 {_SVG_WIDTH} {_SVG_HEIGHT}" '
        'font-family="system-ui, sans-serif">',
        # Moment panel
        _panel_frame(
            _PANEL_TOPS[0],
            "Момент (mom_min, регистр 1991) + порог 0.4×med24",
        ),
        _repair_bands(
            [r for r in result.rod_break_repairs if r.is_rod_break],
            t0,
            span_s,
            _PANEL_TOPS[0],
            _COL_ROD_BREAK_REPAIR,
        ),
        _repair_bands(
            result.other_repairs,
            t0,
            span_s,
            _PANEL_TOPS[0],
            _COL_OTHER_REPAIR,
        ),
        _y_ticks(mom_range, _PANEL_TOPS[0]),
        mom_thr,
        base_line,
        mom_line,
        _episode_markers(result.episodes, t0, span_s, _PANEL_TOPS[0]),
        # Speed panel
        _panel_frame(_PANEL_TOPS[1], "Скорость (spd, регистр 1998) + порог 0.4×med24"),
        _repair_bands(
            [r for r in result.rod_break_repairs if r.is_rod_break],
            t0,
            span_s,
            _PANEL_TOPS[1],
            _COL_ROD_BREAK_REPAIR,
        ),
        _repair_bands(
            result.other_repairs,
            t0,
            span_s,
            _PANEL_TOPS[1],
            _COL_OTHER_REPAIR,
        ),
        _y_ticks(spd_range, _PANEL_TOPS[1]),
        spd_thr,
        spd_line,
        _episode_markers(result.episodes, t0, span_s, _PANEL_TOPS[1]),
        _missed_markers(
            result.missed_repair_ids,
            result.rod_break_repairs_in_coverage,
            t0,
            span_s,
            _PANEL_TOPS[1],
        ),
        _x_ticks(t0, t1, _PANEL_TOPS[1] + _PANEL_HEIGHT),
        "</svg>",
    ]
    return "\n".join(p for p in parts if p)


# ============================================================
# HTML
# ============================================================

_LEGEND_HTML = f"""
<div class="legend">
  <span><span class="chip" style="background:{_COL_ROD_BREAK_REPAIR}"></span>ремонт «обрыв»</span>
  <span><span class="chip" style="background:{_COL_OTHER_REPAIR}"></span>другой ремонт</span>
  <span><span class="chip" style="background:{_EPISODE_COLORS[None]}"></span>{_EPISODE_LABELS[None]}</span>
  <span><span class="chip" style="background:{_EPISODE_COLORS["transient"]}"></span>{_EPISODE_LABELS["transient"]}</span>
  <span><span class="chip" style="background:{_EPISODE_COLORS["other_repair"]}"></span>{_EPISODE_LABELS["other_repair"]}</span>
  <span><span class="chip" style="background:{_EPISODE_COLORS["no_repair"]}"></span>{_EPISODE_LABELS["no_repair"]}</span>
  <span><span class="chip" style="background:{_COL_MISSED}"></span>MISSED ремонт (×)</span>
  <span><span class="chip" style="background:{_COL_BASE_MOMENT}"></span>base_moment</span>
</div>
""".strip()

_CSS = """
body { font-family: system-ui, sans-serif; margin: 20px; color: #222; }
h1 { margin: 0 0 10px; font-size: 20px; }
h2 { margin: 24px 0 8px; font-size: 16px; }
.meta { color: #555; font-size: 13px; margin-bottom: 12px; }
.metrics { display: flex; gap: 24px; flex-wrap: wrap; margin: 12px 0; }
.metric { background: #f5f5f5; padding: 8px 12px; border-radius: 4px; font-size: 13px; }
.metric b { font-size: 16px; display: block; }
.legend { font-size: 12px; color: #444; margin: 10px 0; display: flex; gap: 14px; flex-wrap: wrap; }
.chip { display: inline-block; width: 12px; height: 12px; margin-right: 4px;
        vertical-align: middle; border: 1px solid #ccc; }
table { border-collapse: collapse; font-size: 12px; margin-top: 6px; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
tr.tp td { background: #e8f5e9; }
tr.transient td { background: #fff3e0; }
tr.other_repair td { background: #fffde7; }
tr.no_repair td { background: #ffebee; }
tr.missed td { background: #ffebee; }
.pill { display: inline-block; padding: 1px 6px; border-radius: 3px;
        color: white; font-size: 11px; }
""".strip()


def _wrap_html(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html><head>"
        f'<meta charset="utf-8"><title>{escape(title)}</title>'
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )


def _episode_row(ep: Episode, rod_break: Sequence[RepairSpan]) -> str:
    label = _EPISODE_LABELS[ep.fp_class]
    row_class = "tp" if ep.fp_class is None else ep.fp_class
    matched = ""
    if ep.matched_repair_id:
        rep = next((r for r in rod_break if r.id == ep.matched_repair_id), None)
        if rep:
            gap = (rep.start_time - ep.fired_at).days
            matched = f"repair #{rep.id} ({_fmt_date(rep.start_time)}, lead {gap}d)"
    color = _EPISODE_COLORS[ep.fp_class]
    return (
        f'<tr class="{row_class}">'
        f"<td>{_fmt_dt(ep.fired_at)}</td>"
        f"<td>{_fmt_dt(ep.end_ts)}</td>"
        f'<td><span class="pill" style="background:{color}">{escape(label)}</span></td>'
        f"<td>{'persistent' if ep.persistent else 'transient'}</td>"
        f"<td>{escape(matched)}</td>"
        f"</tr>"
    )


def _repair_row(r: RepairSpan, matched_ids: set[int], missed_ids: set[int]) -> str:
    if r.id in matched_ids:
        status, cls = "DETECTED", "tp"
    elif r.id in missed_ids:
        status, cls = "MISSED", "missed"
    else:
        status, cls = "вне покрытия", ""
    end_display = (
        _fmt_dt(r.original_end_time) if r.original_end_time else "— (заглушка +1д)"
    )
    return (
        f'<tr class="{cls}">'
        f"<td>#{r.id}</td>"
        f"<td>{_fmt_dt(r.start_time)}</td>"
        f"<td>{end_display}</td>"
        f"<td>{escape(status)}</td>"
        f"<td>{escape(_shorten(r.work_list))}</td>"
        f"</tr>"
    )


def render_well_page(result: WellResult, path: Path) -> None:
    """HTML-страница по одной скважине: SVG + таблицы эпизодов и ремонтов."""
    tgt = result.target
    well_label = tgt.well_name or f"well_id={tgt.well_id}"
    title = f"R2 backtest — well {tgt.well_id} ({well_label})"

    matched_ids = {
        ep.matched_repair_id for ep in result.episodes if ep.matched_repair_id
    }
    missed_ids = set(result.missed_repair_ids)

    header = [
        f"<h1>{escape(title)}</h1>",
        '<div class="meta">'
        f"station id={tgt.station_id} code={escape(tgt.station_code or '—')} "
        f"type_1900={tgt.station_type_1900 or '—'} · "
        f"покрытие: {_fmt_date(result.coverage_start)} .. {_fmt_date(result.coverage_end)} · "
        f"base_moment: {_fmt_num(result.base_moment)}"
        "</div>",
    ]

    if result.skipped_reason:
        header.append(
            f'<div class="meta"><b>Пропущено:</b> {escape(result.skipped_reason)}</div>',
        )

    metrics_html = (
        '<div class="metrics">'
        f'<div class="metric">TP<b>{result.tp}</b></div>'
        f'<div class="metric">FN<b>{result.fn}</b></div>'
        f'<div class="metric">FP transient<b>{result.fp_transient}</b></div>'
        f'<div class="metric">FP other-repair<b>{result.fp_other_repair}</b></div>'
        f'<div class="metric">FP no-repair<b>{result.fp_no_repair}</b></div>'
        f'<div class="metric">Recall<b>{_fmt_recall(result)}</b></div>'
        f'<div class="metric">Precision strict<b>{_fmt_pct(result.strict_precision)}</b></div>'
        f'<div class="metric">Precision soft<b>{_fmt_pct(result.soft_precision)}</b></div>'
        f'<div class="metric">Ремонты по обрыву<b>{len(result.rod_break_repairs)}</b>'
        f" (в покрытии {len(result.rod_break_repairs_in_coverage)})</div>"
        "</div>"
    )

    body_parts = [
        *header,
        metrics_html,
        _LEGEND_HTML,
        _render_svg(result),
    ]

    if result.episodes:
        rows = "\n".join(
            _episode_row(ep, result.rod_break_repairs) for ep in result.episodes
        )
        body_parts.append(
            f"<h2>Эпизоды ({len(result.episodes)})</h2>"
            "<table><thead><tr>"
            "<th>fired_at</th><th>end_ts</th><th>класс</th>"
            "<th>устойчивость</th><th>совпадение с ремонтом</th>"
            "</tr></thead><tbody>"
            f"{rows}"
            "</tbody></table>",
        )

    if result.rod_break_repairs:
        rows = "\n".join(
            _repair_row(r, matched_ids, missed_ids) for r in result.rod_break_repairs
        )
        body_parts.append(
            f"<h2>Ремонты по обрыву штанги ({len(result.rod_break_repairs)})</h2>"
            "<table><thead><tr>"
            "<th>id</th><th>start_time</th><th>end_time</th>"
            "<th>статус</th><th>work_list</th>"
            "</tr></thead><tbody>"
            f"{rows}"
            "</tbody></table>",
        )

    body_parts.append('<p style="margin-top:20px"><a href="index.html">← index</a></p>')

    path.write_text(_wrap_html(title, "\n".join(body_parts)), encoding="utf-8")


def render_index(
    results: Sequence[WellResult],
    output_dir: Path,
    generated_at: datetime,
) -> None:
    """Сводный index.html: одна строка на well, ссылки на per-well страницы."""
    total_tp = sum(r.tp for r in results)
    total_fn = sum(r.fn for r in results)
    total_ft = sum(r.fp_transient for r in results)
    total_fo = sum(r.fp_other_repair for r in results)
    total_fn_no = sum(r.fp_no_repair for r in results)
    total_fp = total_ft + total_fo + total_fn_no
    # Recall — по РЕМОНТАМ (детектировано в покрытии / всего в покрытии), не по
    # TP-эпизодам: см. WellResult.recall.
    total_detected = sum(
        len(r.rod_break_repairs_in_coverage) - len(r.missed_repair_ids) for r in results
    )
    total_in_cov = sum(len(r.rod_break_repairs_in_coverage) for r in results)
    global_recall = total_detected / total_in_cov if total_in_cov else None
    global_strict = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else None
    global_soft = (
        total_tp / (total_tp + total_ft + total_fn_no)
        if (total_tp + total_ft + total_fn_no)
        else None
    )
    processed = [r for r in results if r.skipped_reason is None]
    skipped = [r for r in results if r.skipped_reason is not None]

    header = (
        "<h1>R2 backtest — сводка</h1>"
        f'<div class="meta">'
        f"Сгенерировано: {generated_at.strftime('%Y-%m-%d %H:%M:%S')} UTC · "
        f"скважин: {len(results)} (обработано {len(processed)}, пропущено {len(skipped)})"
        "</div>"
        '<div class="metrics">'
        f'<div class="metric">TP всего<b>{total_tp}</b></div>'
        f'<div class="metric">FN всего<b>{total_fn}</b></div>'
        f'<div class="metric">FP transient<b>{total_ft}</b></div>'
        f'<div class="metric">FP other-repair<b>{total_fo}</b></div>'
        f'<div class="metric">FP no-repair<b>{total_fn_no}</b></div>'
        f'<div class="metric">Recall<b>{_fmt_pct(global_recall)} ({total_detected}/{total_in_cov})</b></div>'
        f'<div class="metric">Precision strict<b>{_fmt_pct(global_strict)}</b></div>'
        f'<div class="metric">Precision soft<b>{_fmt_pct(global_soft)}</b></div>'
        "</div>"
        f"{_LEGEND_HTML}"
    )

    def _row(r: WellResult) -> str:
        name = r.target.well_name or f"well_{r.target.well_id}"
        link = f'<a href="well_{r.target.well_id}.html">{escape(name)}</a>'
        cov = f"{_fmt_date(r.coverage_start)} .. {_fmt_date(r.coverage_end)}"
        skipped = r.skipped_reason or ""
        return (
            "<tr>"
            f"<td>{link}</td>"
            f"<td>{r.target.well_id}</td>"
            f"<td>{escape(r.target.station_code or '—')}</td>"
            f"<td>{r.target.station_type_1900 or '—'}</td>"
            f"<td>{cov if r.coverage_start else '—'}</td>"
            f"<td>{len(r.rod_break_repairs)}</td>"
            f"<td>{len(r.rod_break_repairs_in_coverage)}</td>"
            f"<td>{r.tp}</td>"
            f"<td>{r.fn}</td>"
            f"<td>{r.fp_transient}</td>"
            f"<td>{r.fp_other_repair}</td>"
            f"<td>{r.fp_no_repair}</td>"
            f"<td>{_fmt_recall(r)}</td>"
            f"<td>{_fmt_pct(r.strict_precision)}</td>"
            f"<td>{_fmt_pct(r.soft_precision)}</td>"
            f"<td>{escape(skipped)}</td>"
            "</tr>"
        )

    table = (
        "<table><thead><tr>"
        "<th>well</th><th>well_id</th><th>station code</th><th>type_1900</th>"
        "<th>покрытие</th><th>ремонты обрыв</th><th>в покрытии</th>"
        "<th>TP</th><th>FN</th>"
        "<th>FP tr</th><th>FP other</th><th>FP none</th>"
        "<th>Recall</th><th>Prec strict</th><th>Prec soft</th>"
        "<th>skipped</th>"
        "</tr></thead><tbody>"
        + "\n".join(_row(r) for r in results)
        + "</tbody></table>"
    )

    (output_dir / "index.html").write_text(
        _wrap_html("R2 backtest — index", header + table),
        encoding="utf-8",
    )
