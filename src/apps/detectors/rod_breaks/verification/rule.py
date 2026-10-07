"""Чистая логика проверки эпизода R2: данные после срабатывания → отметка.

Без I/O. Каждый вызов считает отметку заново по всем данным на момент ``now``,
поэтому повторный прогон идемпотентен, а отметка меняется только с приходом
новых данных.

Порядок решения:

1. Ремонт или статус простоя ABAI с причиной-отказом → «поломка подтверждена».
2. Ложная тревога: годный замер нефти не раньше T0 + 4 ч, нефть ≥ 50 % нормы,
   и привод работал ≥ 90 % времени от T0 до замер + 12 ч.
3. «Скорее всего поломка»: привод полностью стоял ≥ 10 часов в окне 96 ч.
   Если выполнены и 2, и 3 — побеждает то, что установлено раньше по времени
   данных.
4. Окно 96 ч закрылось, а ни 1–3 нет → «не удалось проверить» с причиной.
5. Иначе — «ожидает».
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from apps.detectors.models.verification import (
    REASON_ABAI_STATUS,
    REASON_DRIVE_STOPPED,
    REASON_DRIVE_UNSTABLE,
    REASON_NO_MEASUREMENT,
    REASON_NO_OIL_NORM,
    REASON_NO_STATUS,
    REASON_OIL_LOW,
    REASON_OIL_NOT_MEASURED,
    REASON_OIL_OK_DRIVE_OK,
    REASON_REPAIR,
    REASON_REPAIR_ROD_BREAK,
    VERDICT_FAILURE_CONFIRMED,
    VERDICT_FAILURE_LIKELY,
    VERDICT_FALSE_ALARM,
    VERDICT_PENDING,
    VERDICT_UNDETERMINED,
)
from apps.detectors.rod_breaks.verification import config

_HOUR = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class StatusSample:
    """Отсчёт СДМО с заполненным регистром статуса 1999."""

    at: datetime
    code: int


@dataclass(frozen=True, slots=True)
class Measurement:
    """Замер ЦИТС: нефть т/сут, жидкость м³/сут."""

    at: datetime
    qm_oil: float | None
    qv_liquid: float | None


@dataclass(frozen=True, slots=True)
class RepairEvent:
    repair_id: int
    start: datetime
    rod_break: bool
    work_list: str


@dataclass(frozen=True, slots=True)
class AbaiStatusEvent:
    """Интервал статуса ABAI (время уже местное; until=None — открыт)."""

    since: datetime
    until: datetime | None
    code: str | None
    reason: str | None

    def covers(self, moment: datetime) -> bool:
        return self.since <= moment and (self.until is None or moment < self.until)


@dataclass(frozen=True, slots=True)
class VerificationInput:
    t0: datetime
    status_samples: Sequence[StatusSample]
    measurements: Sequence[Measurement]
    tech_regime_oil: float | None
    repairs: Sequence[RepairEvent]
    abai_statuses: Sequence[AbaiStatusEvent]


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verdict: str
    reason: str | None
    is_final: bool
    evidence_at: datetime | None
    evidence: dict = field(default_factory=dict)


# --- Признаки ---


def is_abai_failure(event: AbaiStatusEvent) -> bool:
    """Статус ABAI означает отказ: сам статус или причина простоя."""
    if event.code in config.ABAI_FAILURE_STATUS_CODES:
        return True
    if event.code in config.ABAI_WORK_STATUS_CODES:
        return False
    reason = (event.reason or "").lower()
    return any(pattern in reason for pattern in config.ABAI_FAILURE_REASON_PATTERNS)


def is_valid_measurement(m: Measurement) -> bool:
    """Нефть заполнена, не отрицательная и не больше жидкости (мусор ЦИТС)."""
    if m.qm_oil is None or m.qm_oil < 0:
        return False
    return not (m.qv_liquid is not None and m.qv_liquid > 0 and m.qm_oil > m.qv_liquid)


def oil_norm(inp: VerificationInput) -> tuple[float | None, str | None]:
    """Обычная нефть: большее из техрежима и медианы замеров за 14 суток до T0.

    Большее — чтобы просевшая перед отказом медиана не занизила порог.
    """
    since = inp.t0 - timedelta(days=config.OIL_NORM_LOOKBACK_DAYS)
    before = [
        m.qm_oil
        for m in inp.measurements
        if since < m.at <= inp.t0 and is_valid_measurement(m)
    ]
    candidates: list[tuple[float, str]] = []
    if inp.tech_regime_oil is not None and inp.tech_regime_oil > 0:
        candidates.append((inp.tech_regime_oil, "tech_regime"))
    if before:
        med = median(before)
        if med > 0:
            candidates.append((med, "median_14d"))
    if not candidates:
        return None, None
    return max(candidates, key=lambda c: c[0])


def work_share(
    samples: Sequence[StatusSample],
    start: datetime,
    end: datetime,
) -> tuple[float | None, int]:
    """Доля отсчётов «привод работает» в [start, end) и число отсчётов."""
    window = [s for s in samples if start <= s.at < end]
    if not window:
        return None, 0
    working = sum(1 for s in window if s.code in config.DRIVE_WORK_CODES)
    return working / len(window), len(window)


def stopped_hours_reached(
    samples: Sequence[StatusSample],
    start: datetime,
    end: datetime,
    need: int,
) -> tuple[datetime | None, int]:
    """Когда набралось ``need`` часов полной остановки в [start, end).

    Час остановки — час, в котором все отсчёты статуса не «работает». Часы
    без отсчётов не считаются ни работой, ни остановкой. Возвращает конец
    часа, замкнувшего счёт (или None), и сколько часов остановки набралось.
    """
    hours: dict[datetime, bool] = {}
    for s in samples:
        if not (start <= s.at < end):
            continue
        hour = s.at.replace(minute=0, second=0, microsecond=0)
        working = s.code in config.DRIVE_WORK_CODES
        hours[hour] = hours.get(hour, False) or working
    stopped = 0
    reached_at = None
    for hour in sorted(hours):
        if not hours[hour]:
            stopped += 1
            if stopped == need and reached_at is None:
                reached_at = hour + _HOUR
    return reached_at, stopped


# --- Ветки решения ---


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _confirmation(inp: VerificationInput, now: datetime) -> VerificationResult | None:
    """Ремонт или статус простоя ABAI после тревоги.

    Статус-отказ, уже стоявший на момент тревоги (например, «Нет подачи» с
    позавчера — в КМГ привод при этом часто не выключают), тоже подтверждает:
    правило поймало известную поломку.
    """
    until = min(now, inp.t0 + timedelta(days=config.FINAL_DAYS))
    repairs = [
        r
        for r in inp.repairs
        if inp.t0 - timedelta(hours=config.REPAIR_LOOKBACK_HOURS) <= r.start <= until
    ]
    statuses = [
        s
        for s in inp.abai_statuses
        if is_abai_failure(s)
        and (
            inp.t0 - timedelta(hours=config.ABAI_LOOKBACK_HOURS) <= s.since <= until
            or s.covers(inp.t0)
        )
    ]
    if not repairs and not statuses:
        return None

    rod_breaks = [r for r in repairs if r.rod_break]
    if rod_breaks:
        reason = REASON_REPAIR_ROD_BREAK
        evidence_at = min(r.start for r in rod_breaks)
    elif repairs:
        reason = REASON_REPAIR
        evidence_at = min(r.start for r in repairs)
    else:
        reason = REASON_ABAI_STATUS
        evidence_at = min(s.since for s in statuses)

    evidence: dict = {}
    if repairs:
        evidence["repair"] = [
            {
                "repair_id": r.repair_id,
                "start": _iso(r.start),
                "rod_break": r.rod_break,
                "work_list": r.work_list[:200],
            }
            for r in sorted(repairs, key=lambda r: r.start)
        ]
    if statuses:
        evidence["abai"] = [
            {
                "since": _iso(s.since),
                "until": _iso(s.until),
                "status": s.code,
                "reason": s.reason,
            }
            for s in sorted(statuses, key=lambda s: s.since)
        ]
    return VerificationResult(
        verdict=VERDICT_FAILURE_CONFIRMED,
        reason=reason,
        is_final=True,
        evidence_at=evidence_at,
        evidence=evidence,
    )


def _block_at(inp: VerificationInput) -> datetime | None:
    """Первое после T0 событие, после которого замеры описывают другое состояние.

    Любой ремонт и любой статус «не в работе» (в т.ч. отключение
    электроэнергии): после них нефть в замере уже не про эту тревогу.
    """
    moments = [r.start for r in inp.repairs if r.start > inp.t0]
    moments += [
        s.since
        for s in inp.abai_statuses
        if s.since > inp.t0 and s.code not in config.ABAI_WORK_STATUS_CODES
    ]
    return min(moments) if moments else None


@dataclass(frozen=True, slots=True)
class _OilCheck:
    """Итог поиска «нефть в норме + привод работал»."""

    decided_at: datetime | None = None  # замер + 12 ч, если ложная доказана
    waiting: Measurement | None = None  # годный замер, ждём 12 ч работы привода
    measurement: Measurement | None = None
    drive_share: float | None = None
    drive_samples: int = 0
    rejected_by_drive: bool = False  # нефть в норме, но привод работал < порога


def _measurement_window(
    inp: VerificationInput,
    block: datetime | None,
) -> tuple[datetime, datetime]:
    lo = inp.t0 + timedelta(hours=config.MEASUREMENT_MIN_DELAY_HOURS)
    hi = inp.t0 + timedelta(hours=config.WINDOW_HOURS)
    if block is not None:
        hi = min(hi, block)
    return lo, hi


def _oil_check(
    inp: VerificationInput,
    now: datetime,
    norm: float,
    block: datetime | None,
) -> _OilCheck:
    lo, hi = _measurement_window(inp, block)
    good = sorted(
        (
            m
            for m in inp.measurements
            if lo < m.at <= hi
            and is_valid_measurement(m)
            and m.qm_oil
            and m.qm_oil >= config.OIL_SHARE_MIN * norm
        ),
        key=lambda m: m.at,
    )
    after = timedelta(hours=config.DRIVE_AFTER_MEASUREMENT_HOURS)
    rejected = False
    for m in good:
        t_end = m.at + after
        if block is not None and t_end > block:
            continue
        if t_end > now:
            return _OilCheck(waiting=m, rejected_by_drive=rejected)
        share, n = work_share(inp.status_samples, inp.t0, t_end)
        if share is not None and share >= config.DRIVE_WORK_SHARE_MIN:
            return _OilCheck(
                decided_at=t_end,
                measurement=m,
                drive_share=share,
                drive_samples=n,
            )
        rejected = True
    return _OilCheck(rejected_by_drive=rejected)


def _undetermined_reason(
    inp: VerificationInput,
    norm: float | None,
    block: datetime | None,
    oil: _OilCheck | None,
) -> str:
    window_end = inp.t0 + timedelta(hours=config.WINDOW_HOURS)
    if not any(inp.t0 <= s.at < window_end for s in inp.status_samples):
        return REASON_NO_STATUS
    lo, hi = _measurement_window(inp, block)
    in_window = [m for m in inp.measurements if lo < m.at <= hi]
    if not in_window:
        return REASON_NO_MEASUREMENT
    with_oil = [m for m in in_window if is_valid_measurement(m) and m.qm_oil]
    if not with_oil:
        return REASON_OIL_NOT_MEASURED
    if norm is None:
        return REASON_NO_OIL_NORM
    if oil is not None and oil.rejected_by_drive:
        return REASON_DRIVE_UNSTABLE
    return REASON_OIL_LOW


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def evaluate(inp: VerificationInput, now: datetime) -> VerificationResult:
    """Отметка эпизода на момент ``now`` (местное время, наивное)."""
    final = now >= inp.t0 + timedelta(days=config.FINAL_DAYS)

    confirmed = _confirmation(inp, now)
    if confirmed is not None:
        return confirmed

    block = _block_at(inp)
    norm, norm_source = oil_norm(inp)
    oil = _oil_check(inp, now, norm, block) if norm is not None else None

    window_end = inp.t0 + timedelta(hours=config.WINDOW_HOURS)
    stop_until = min(now, window_end)
    likely_at, stopped_hours = stopped_hours_reached(
        inp.status_samples,
        inp.t0,
        stop_until,
        config.DRIVE_STOPPED_HOURS_LIKELY,
    )
    window = {
        "t0": _iso(inp.t0),
        "measurement_from": _iso(
            inp.t0 + timedelta(hours=config.MEASUREMENT_MIN_DELAY_HOURS),
        ),
        "measurement_to": _iso(_measurement_window(inp, block)[1]),
        "block_at": _iso(block),
    }

    # Ложная и «скорее всего поломка» — побеждает установленное раньше.
    if (
        oil is not None
        and oil.decided_at is not None
        and (likely_at is None or oil.decided_at <= likely_at)
    ):
        m = oil.measurement
        return VerificationResult(
            verdict=VERDICT_FALSE_ALARM,
            reason=REASON_OIL_OK_DRIVE_OK,
            is_final=final,
            evidence_at=oil.decided_at,
            evidence={
                "window": window,
                "oil": {
                    "measured_at": _iso(m.at),
                    "qm_oil": _round(m.qm_oil),
                    "qv_liquid": _round(m.qv_liquid),
                    "norm": _round(norm),
                    "norm_source": norm_source,
                    "share_of_norm": _round(m.qm_oil / norm),
                },
                "drive": {
                    "from": _iso(inp.t0),
                    "to": _iso(oil.decided_at),
                    "work_share": _round(oil.drive_share),
                    "samples": oil.drive_samples,
                },
            },
        )

    if likely_at is not None:
        return VerificationResult(
            verdict=VERDICT_FAILURE_LIKELY,
            reason=REASON_DRIVE_STOPPED,
            is_final=final,
            evidence_at=likely_at,
            evidence={
                "window": window,
                "drive": {
                    "from": _iso(inp.t0),
                    "to": _iso(stop_until),
                    "stopped_hours": stopped_hours,
                    "threshold_hours": config.DRIVE_STOPPED_HOURS_LIKELY,
                },
            },
        )

    if oil is not None and oil.waiting is not None:
        m = oil.waiting
        return VerificationResult(
            verdict=VERDICT_PENDING,
            reason=None,
            is_final=False,
            evidence_at=None,
            evidence={
                "window": window,
                "waiting": "drive_after_measurement",
                "oil": {
                    "measured_at": _iso(m.at),
                    "qm_oil": _round(m.qm_oil),
                    "norm": _round(norm),
                    "norm_source": norm_source,
                },
                "decide_at": _iso(
                    m.at + timedelta(hours=config.DRIVE_AFTER_MEASUREMENT_HOURS),
                ),
            },
        )

    if now >= window_end:
        share, n = work_share(inp.status_samples, inp.t0, window_end)
        return VerificationResult(
            verdict=VERDICT_UNDETERMINED,
            reason=_undetermined_reason(inp, norm, block, oil),
            is_final=final,
            evidence_at=window_end,
            evidence={
                "window": window,
                "oil": {"norm": _round(norm), "norm_source": norm_source},
                "drive": {"work_share": _round(share), "samples": n},
            },
        )

    return VerificationResult(
        verdict=VERDICT_PENDING,
        reason=None,
        is_final=False,
        evidence_at=None,
        evidence={"window": window, "waiting": "measurement"},
    )
