"""Тексты граф ведомости из собранного контекста строки — детерминированно.

LLM здесь нет: всё выводится из эпизода, дебитов и ремонтов по шаблонам.
"""

from datetime import date, datetime, timedelta

from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_STATUS_NORMALIZED,
)
from apps.detectors.services.daily_sheet.config import (
    DEVIATION_LABELS,
    POST_REPAIR_DAYS,
    R2_RATIO_STRONG,
    R2_TOTAL_LOSS_SUFFIX,
    SEVERITY_LABELS,
    WATERED_OUT_CUT,
)
from apps.detectors.services.daily_sheet.context import RepairInfo, WellContext

# Правила, эпизод которых считается сутками: в периодах только даты.
_DAILY_CODES = frozenset({"R9", "R10"})


# --- форматирование ----------------------------------------------------------


def fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_day(at: datetime) -> str:
    return at.strftime("%d.%m")


def fmt_dt(at: datetime) -> str:
    return at.strftime("%d.%m.%Y, %H:%M")


def _end_day(last_seen_at: datetime) -> datetime:
    """Правая граница корзины — полночь следующих суток; показываем сами сутки."""
    return last_seen_at - timedelta(seconds=1)


def span_text(
    opened_at: datetime,
    last_seen_at: datetime,
    *,
    dates_only: bool,
) -> str:
    """«сутки 27.08», «период 26.08 – 29.08» или с временем для R2."""
    if dates_only:
        start, end = fmt_day(opened_at), fmt_day(_end_day(last_seen_at))
        return f"сутки {start}" if start == end else f"период {start} – {end}"
    start = opened_at.strftime("%d.%m %H:%M")
    end = (
        last_seen_at.strftime("%H:%M")
        if last_seen_at.date() == opened_at.date()
        else last_seen_at.strftime("%d.%m %H:%M")
    )
    return f"период {start} – {end}"


# --- графы -------------------------------------------------------------------


def period_text(
    ctx: WellContext,
    *,
    day_end: datetime,
    partial_day: bool,
) -> str:
    """«период 26.08 – 29.08, 4 сут» и варианты; активен ли эпизод — в графе
    «Статус на дату» (status_text).

    Активный эпизод без сработки в сами сутки ведомости (правило держит его
    открытым до подтверждения восстановления) помечается «без сработок с …»,
    чтобы технолог не принял хвост гистерезиса за длящееся отклонение.
    """
    episode = ctx.primary
    incident = episode.incident
    dates_only = ctx.detector_code in _DAILY_CODES
    sheet_date = (day_end - timedelta(days=1)).date()
    last_seen = min(incident.last_seen_at, day_end)
    seen_day = _end_day(last_seen).date() if dates_only else last_seen.date()
    text = span_text(incident.opened_at, last_seen, dates_only=dates_only)
    if episode.status != INCIDENT_STATUS_NORMALIZED:
        if seen_day < sheet_date:
            text += f", без сработок с {seen_day + timedelta(days=1):%d.%m}"
        else:
            duration = (sheet_date - incident.opened_at.date()).days + 1
            if duration > 1:
                text += f", {duration} сут"
            if partial_day:
                text += ", по неполным суткам"
    parts = [text]
    others = [
        span_text(
            e.incident.opened_at,
            min(e.incident.last_seen_at, day_end),
            dates_only=dates_only,
        )
        for e in ctx.episodes[1:]
    ]
    if others:
        parts.append("также " + ", ".join(others))
    if ctx.previous:
        parts.append(
            "повторно: пред. проявление "
            + span_text(
                ctx.previous[0].opened_at,
                ctx.previous[0].last_seen_at,
                dates_only=dates_only,
            )
            .removeprefix("период ")
            .removeprefix("сутки "),
        )
    return "; ".join(parts)


def deviation_text(
    ctx: WellContext,
    *,
    day_end: datetime,
    partial_day: bool,
) -> str:
    label = DEVIATION_LABELS.get(ctx.detector_code, ctx.detector_code)
    payload = ctx.primary.incident.payload or {}
    if ctx.detector_code == "R2":
        ratio = payload.get("current_ratio")
        if ratio is not None and ratio <= R2_RATIO_STRONG:
            label += R2_TOTAL_LOSS_SUFFIX
    if ctx.detector_code == "R10" and payload.get("klass"):
        label += f" ({payload['klass']})"
    text = (
        f"{label}, {SEVERITY_LABELS[ctx.severity]}, "
        f"{period_text(ctx, day_end=day_end, partial_day=partial_day)}"
    )
    if ctx.losses:
        text += (
            "; потери сохраняются"
            if ctx.primary.status == INCIDENT_STATUS_NORMALIZED
            else "; с потерями добычи"
        )
    return text


def status_text(ctx: WellContext) -> str:
    """Горит ли тревога на конец суток ведомости: «Активен · авария» /
    «Активен · предупреждение» / «Завершён 20.09 15:45»."""
    episode = ctx.primary
    normalized_at = episode.incident.normalized_at
    if episode.status == INCIDENT_STATUS_NORMALIZED and normalized_at is not None:
        if ctx.detector_code in _DAILY_CODES:
            return f"Завершён {fmt_day(_end_day(normalized_at))}"
        return f"Завершён {normalized_at:%d.%m %H:%M}"
    level = "авария" if episode.level == INCIDENT_LEVEL_ALARM else "предупреждение"
    return f"Активен · {level}"


def post_repair(ctx: WellContext) -> RepairInfo | None:
    """Ремонт, закончившийся не раньше POST_REPAIR_DAYS до открытия эпизода."""
    opened = ctx.primary.incident.opened_at
    for repair in ctx.repairs:
        if repair.end is None:
            continue
        if repair.end <= opened <= repair.end + timedelta(days=POST_REPAIR_DAYS):
            return repair
    return None


def cause_text(ctx: WellContext) -> str:
    qualifiers = []
    repair = post_repair(ctx)
    if repair is not None and repair.end is not None:
        qualifiers.append(f"пуск после ремонта {fmt_day(repair.end)}")
    rates = ctx.rates
    if rates.has_fresh:
        if (
            rates.oil is not None
            and rates.oil <= 0
            and rates.water_cut is not None
            and rates.water_cut >= WATERED_OUT_CUT
        ):
            qualifiers.append("обводнение — нефти нет")
        elif ctx.losses:
            qualifiers.append("падение добычи относительно режима")
        elif ctx.liquid_ratio is not None and ctx.liquid_ratio >= 1:
            qualifiers.append("потерь добычи пока нет")
    return ctx.cause + ("; " + "; ".join(qualifiers) if qualifiers else "")


def rates_text(ctx: WellContext) -> str:
    rates = ctx.rates
    fact = fmt_num(rates.liquid) if rates.has_fresh else "нет замеров"
    return f"{fact} / {fmt_num(rates.plan_liquid)}"


def plan_oil_text(ctx: WellContext) -> str:
    return fmt_num(ctx.rates.plan_oil, 2)


def top_text(ctx: WellContext) -> str:
    """Строка ТОП: скважина, вероятность и главное из основания."""
    lead = "; ".join([_rates_sentence(ctx), _rule_sentence(ctx)])
    lead = lead[:1].upper() + lead[1:]
    return f"{ctx.well_name} — {ctx.probability_percent}%. {lead}."


def attention_text(ctx: WellContext) -> str:
    return (
        f"{ctx.well_name} — {SEVERITY_LABELS[ctx.severity]}, "
        f"{_rates_sentence(ctx)} ({ctx.probability_percent}%)"
    )


# --- предложения «Основания» -------------------------------------------------


def _rule_sentence(ctx: WellContext) -> str:
    payload = ctx.primary.incident.payload or {}
    if ctx.detector_code == "R9":
        k, k_alert = payload.get("k"), payload.get("k_alert")
        baseline = payload.get("baseline") or {}
        text = f"K = {fmt_num(k, 2)} при пороге {fmt_num(k_alert, 2)}"
        if baseline.get("n_days"):
            text += f", база {baseline['n_days']} сут"
        if len(payload.get("branches") or []) > 1:
            text += ", обе ветки"
        return text + " (СДМО)"
    if ctx.detector_code == "R2":
        ratio, base = payload.get("current_ratio"), payload.get("base_moment")
        text = "момент "
        text += f"{ratio * 100:.0f} % от базы" if ratio is not None else "ниже базы"
        if base is not None:
            text += f" {fmt_num(base)}"
        text += " при сохранённой скорости"
        if payload.get("low_confidence"):
            text += ", база слабая"
        return text + " (СДМО)"
    if ctx.detector_code == "R10":
        return _r10_rule_sentence(payload)
    return f"сигнал {ctx.primary.incident.reason_code} (СДМО)"


def _r10_rule_sentence(payload: dict) -> str:
    since = payload.get("series_from")
    since = f" с {date.fromisoformat(since):%d.%m}" if since else ""
    n = payload.get("n_series")
    rezhim = fmt_num(payload.get("rezhim"))
    if payload.get("dev") is None:
        text = f"нулевых замеров подряд: {n}{since}, техрежим {rezhim}"
        if payload.get("su"):
            text += f"; {payload['su']}"
    else:
        text = (
            f"Qж {fmt_num(payload.get('q_last'))} при техрежиме {rezhim} "
            f"({payload['dev'] * 100:+.0f} %), замеров за порогом подряд: {n}{since}"
        )
        if payload.get("prev_dev") is not None:
            text += f"; до серии {payload['prev_dev'] * 100:+.0f} %"
    if payload.get("note"):
        text += f"; {payload['note']}"
    return text + " (ЦИТС)"


def measure_request_text(well_name: str, payload: dict) -> str:
    """R10, «замер устарел»: когда был последний замер и что с СУ."""
    last = payload.get("last_day")
    last = f"{date.fromisoformat(last):%d.%m}" if last else "—"
    su = payload.get("su") or "нет данных СУ"
    text = (
        f"{well_name} — последний замер {last} "
        f"({payload.get('age_d', '—')} сут назад); {su}"
    )
    if "периодическая" in (payload.get("note") or ""):
        text += "; периодическая эксплуатация"
    return text


def _rates_sentence(ctx: WellContext) -> str:
    rates = ctx.rates
    if not rates.has_fresh:
        since = f" с {fmt_day(rates.measured_at)}" if rates.measured_at else ""
        return f"замеров дебита нет{since} (ТМ)"
    parts = [f"жидкость {fmt_num(rates.liquid)} м3/сут"]
    if rates.plan_liquid is not None:
        parts[0] += f" при режиме {fmt_num(rates.plan_liquid)}"
    if rates.oil is not None:
        oil = f"нефть {fmt_num(rates.oil, 2)} т/сут"
        if rates.plan_oil is not None:
            oil += f" при плане {fmt_num(rates.plan_oil, 2)}"
        parts.append(oil)
    measured = fmt_day(rates.measured_at) if rates.measured_at else ""
    return ", ".join(parts) + f" ({measured}, ТМ)"
