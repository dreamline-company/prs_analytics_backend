"""Чистая логика проверки эпизода R2: данные после срабатывания → итог.

Без I/O. Каждый вызов считает отметку заново по всем данным на момент ``now``,
поэтому повторный прогон идемпотентен, а отметка меняется только с приходом
новых данных.

Правило заказчика (19.11.2026, ``r2-verify-v3``): «Подтверждено» — только ПРС
с обрывом штанг в описании работ, всё остальное — «Не подтверждено» с
причиной. Порядок решения:

1. Момент вернулся: средний момент в окне T0 + 1…6 ч ≥ 75 % среднего за 24 ч
   до T0 → «Не подтверждено» (``moment_recovered``), окончательно в T0 + 6 ч.
2. Первый ремонт после сигнала (или ремонт, шедший на T0):
   ПРС с обрывом штанг → «Подтверждено»; шёл на T0 → «Не подтверждено»
   (``during_repair``); другого вида → «Не подтверждено» (``repair_other``)
   после закрытия ремонта + 2 сут (работы дописывают позже), до того — ждём.
3. Годный замер нефти не раньше T0 + 4 ч, нефть ≥ 50 % нормы, привод работал
   ≥ 90 % до замер + 12 ч → «Не подтверждено» (``oil_ok_drive_ok``).
4. Прошло 45 сут, ПРС с обрывом не было → «Не подтверждено»
   (``no_rod_break_repair``).
5. Иначе — «Ожидает подтверждения» (в доказательствах — что известно: часы
   остановки привода, простой ABAI).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from statistics import fmean, median

from apps.detectors.models.verification import (
    REASON_DURING_REPAIR,
    REASON_MOMENT_RECOVERED,
    REASON_NO_ROD_BREAK_REPAIR,
    REASON_OIL_OK_DRIVE_OK,
    REASON_REPAIR_OTHER,
    REASON_REPAIR_ROD_BREAK,
    VERDICT_CONFIRMED,
    VERDICT_NOT_CONFIRMED,
    VERDICT_PENDING,
)
from apps.detectors.rod_breaks import config as r2_config
from apps.detectors.rod_breaks import incident_config
from apps.detectors.rod_breaks.verification import config

_REPAIR_OPEN_MAX = timedelta(days=incident_config.REPAIR_OPEN_MAX_DAYS)

_HOUR = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class StatusSample:
    """Отсчёт СДМО с заполненным регистром статуса 1999."""

    at: datetime
    code: int


@dataclass(frozen=True, slots=True)
class MomentSample:
    """Отсчёт СДМО: момент штанги (регистр 1991) и скорость ротора (1998)."""

    at: datetime
    moment: float
    speed: float | None


@dataclass(frozen=True, slots=True)
class Measurement:
    """Замер ЦИТС: нефть т/сут, жидкость м³/сут."""

    at: datetime
    qm_oil: float | None
    qv_liquid: float | None


_ROD_EVENT_RE = re.compile(config.ROD_BREAK_EVENTS)
_ROD_PART_RE = re.compile(config.ROD_BREAK_PARTS)
_NOT_ROD_RE = re.compile(config.ROD_BREAK_NOT_PARTS)
_PHRASE_END_RE = re.compile(r"[.;]")


def mentions_rod_break(text: str) -> bool:
    """В тексте работ — обрыв (отворот, слом, срыв резьбы) штанги.

    Событие, а следом в той же фразе (≤ ``ROD_BREAK_GAP`` символов) — штанга,
    шток или центратор; НКТ, кабель, ротор, плунжер между ними — не штанги.
    Примеры с прода: «обрыв 34-ой штанги 22мм», «обрыв 61-штанги», «обрыв
    короткий штаги», «слом на 17-ой штанге», «срыв резьбы 16-ой штанги».
    """
    low = text.lower().replace("ё", "е")
    for event in _ROD_EVENT_RE.finditer(low):
        tail = low[event.end() : event.end() + config.ROD_BREAK_GAP]
        tail = _PHRASE_END_RE.split(tail, maxsplit=1)[0]
        part = _ROD_PART_RE.search(tail)
        if part and not _NOT_ROD_RE.search(tail[: part.start()]):
            return True
    return False


@dataclass(frozen=True, slots=True)
class RepairEvent:
    """Ремонт ABAI (время местное; ``end=None`` — не закрыт)."""

    repair_id: int
    start: datetime
    work_list: str
    end: datetime | None = None
    abai_type_id: int | None = None
    type_name: str = ""

    def covers(self, moment: datetime) -> bool:
        """Ремонт начался раньше и ещё шёл в ``moment``.

        Не закрытый в ABAI ремонт считается идущим не дольше
        ``REPAIR_OPEN_MAX_DAYS`` (как в прогоне R2): висящие ремонты без конца
        иначе забирали бы все последующие сигналы.
        """
        end = self.end or self.start + _REPAIR_OPEN_MAX
        return self.start < moment < end

    @property
    def is_prs(self) -> bool:
        if self.abai_type_id is not None:
            return self.abai_type_id == config.ABAI_REPAIR_TYPE_PRS
        return self.type_name.lower().startswith(config.PRS_WORK_TYPE_PREFIX)

    @property
    def has_text(self) -> bool:
        return bool(self.work_list.strip())

    @property
    def rod_break(self) -> bool:
        """Обрыв штанг: по тексту работ, а без текста — по виду работ."""
        if self.has_text:
            return mentions_rod_break(self.work_list)
        return config.ROD_BREAK_WORK_TYPE in self.type_name.lower()


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
    moment_samples: Sequence[MomentSample] = ()


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


@dataclass(frozen=True, slots=True)
class _MomentCheck:
    """Итог сравнения момента после тревоги с моментом до неё."""

    decided_at: datetime  # конец окна T0 + 6 ч
    recovered: bool
    evidence: dict


def _clip_moment(value: float) -> float:
    return min(max(value, r2_config.MOMENT_CLIP_MIN), r2_config.MOMENT_CLIP_MAX)


def moment_check(inp: VerificationInput, now: datetime) -> _MomentCheck | None:
    """Вернулся ли момент после тревоги; ``None`` — окно T0+1…6 ч не закрылось.

    База — средний момент за 24 ч до T0 по отсчётам, где ротор крутился
    (скорость > ``FAILURE_SPD_ABS``): остановки не занижают обычный уровень.
    После — средний момент всех отсчётов окна: если привод после тревоги
    остановили, это тоже не «вернулся».
    """
    decided_at = inp.t0 + timedelta(hours=config.MOMENT_RECOVERY_TO_HOURS)
    if now < decided_at:
        return None
    base_from = inp.t0 - timedelta(hours=config.MOMENT_BASE_HOURS)
    window_from = inp.t0 + timedelta(hours=config.MOMENT_RECOVERY_FROM_HOURS)
    before = [
        _clip_moment(s.moment)
        for s in inp.moment_samples
        if base_from <= s.at < inp.t0
        and s.speed is not None
        and s.speed > r2_config.FAILURE_SPD_ABS
    ]
    after = [
        _clip_moment(s.moment)
        for s in inp.moment_samples
        if window_from <= s.at < decided_at
    ]
    base = fmean(before) if before else None
    level = fmean(after) if after else None
    share = level / base if base and level is not None else None
    enough = (
        len(before) >= config.MOMENT_BASE_MIN_SAMPLES
        and len(after) >= config.MOMENT_WINDOW_MIN_SAMPLES
    )
    recovered = (
        enough
        and base is not None
        and base >= config.MOMENT_BASE_MIN
        and share is not None
        and share >= config.MOMENT_RECOVERY_SHARE_MIN
    )
    return _MomentCheck(
        decided_at=decided_at,
        recovered=recovered,
        evidence={
            "base": _round(base, 1),
            "base_samples": len(before),
            "base_from": _iso(base_from),
            "window_from": _iso(window_from),
            "window_to": _iso(decided_at),
            "level": _round(level, 1),
            "samples": len(after),
            "share_of_base": _round(share),
            "threshold": config.MOMENT_RECOVERY_SHARE_MIN,
        },
    )


def _with_moment(
    result: VerificationResult,
    moment: _MomentCheck | None,
) -> VerificationResult:
    """Цифры момента — в доказательства любой отметки (для калибровки порога)."""
    if moment is None or "moment" in result.evidence:
        return result
    return replace(result, evidence={**result.evidence, "moment": moment.evidence})


# --- Ветки решения ---


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


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


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def evaluate(inp: VerificationInput, now: datetime) -> VerificationResult:
    """Отметка эпизода на момент ``now`` (местное время, наивное)."""
    moment = moment_check(inp, now)
    return _with_moment(_evaluate(inp, now, moment), moment)


def first_repair(inp: VerificationInput) -> RepairEvent | None:
    """Первый ремонт эпизода: начатый после T0 (не позже окна ПРС) или шедший
    на T0 (начат не раньше ``COVER_LOOKBACK_DAYS``)."""
    until = inp.t0 + timedelta(days=config.PRS_WAIT_DAYS)
    cover_from = inp.t0 - timedelta(days=config.COVER_LOOKBACK_DAYS)
    candidates = [
        r
        for r in inp.repairs
        if inp.t0 <= r.start <= until or (r.start >= cover_from and r.covers(inp.t0))
    ]
    return min(candidates, key=lambda r: r.start, default=None)


def _repair_evidence(repair: RepairEvent) -> dict:
    return {
        "repair_id": repair.repair_id,
        "start": _iso(repair.start),
        "end": _iso(repair.end),
        "is_prs": repair.is_prs,
        "rod_break": repair.rod_break,
        "type_name": repair.type_name[:120],
        "work_list": repair.work_list[:200],
    }


def _result(
    verdict: str,
    reason: str | None,
    evidence_at: datetime | None,
    evidence: dict,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict,
        reason=reason,
        is_final=verdict != VERDICT_PENDING,
        evidence_at=evidence_at,
        evidence=evidence,
    )


def _repair_verdict(
    inp: VerificationInput,
    repair: RepairEvent,
    now: datetime,
    evidence: dict,
) -> VerificationResult:
    """Итог по первому ремонту: подтверждён ли обрыв штанг."""
    evidence = {**evidence, "repair": [_repair_evidence(repair)]}
    wait_until = inp.t0 + timedelta(days=config.PRS_WAIT_DAYS)
    # Работы ещё не записаны, ремонт идёт — решать не по чему; после окна
    # ожидания решает вид работ (висящий в ABAI ремонт без текста).
    if not repair.has_text and repair.end is None and now < wait_until:
        return _result(
            VERDICT_PENDING,
            None,
            None,
            {**evidence, "waiting": "repair_works"},
        )
    if repair.is_prs and repair.rod_break:
        return _result(
            VERDICT_CONFIRMED,
            REASON_REPAIR_ROD_BREAK,
            repair.start,
            evidence,
        )
    if repair.covers(inp.t0):
        return _result(
            VERDICT_NOT_CONFIRMED,
            REASON_DURING_REPAIR,
            inp.t0,
            evidence,
        )
    grace = timedelta(days=config.REPAIR_TEXT_GRACE_DAYS)
    if repair.end is not None and now >= repair.end + grace:
        return _result(
            VERDICT_NOT_CONFIRMED,
            REASON_REPAIR_OTHER,
            repair.end,
            evidence,
        )
    if now >= wait_until:
        return _result(
            VERDICT_NOT_CONFIRMED,
            REASON_REPAIR_OTHER,
            wait_until,
            evidence,
        )
    # Ремонт другого вида не закрыт (или закрыт только что): работы ещё
    # дописывают, обрыв может появиться.
    return _result(
        VERDICT_PENDING,
        None,
        None,
        {**evidence, "waiting": "repair_works"},
    )


def _interim(inp: VerificationInput, now: datetime) -> dict:
    """Что известно, пока ждём: остановка привода и простой ABAI."""
    window_end = inp.t0 + timedelta(hours=config.WINDOW_HOURS)
    stop_until = min(now, window_end)
    _, stopped = stopped_hours_reached(
        inp.status_samples,
        inp.t0,
        stop_until,
        config.DRIVE_STOPPED_HOURS_LIKELY,
    )
    share, samples = work_share(inp.status_samples, inp.t0, stop_until)
    evidence: dict = {
        "drive": {
            "from": _iso(inp.t0),
            "to": _iso(stop_until),
            "stopped_hours": stopped,
            "work_share": _round(share),
            "samples": samples,
        },
    }
    failures = [
        s
        for s in inp.abai_statuses
        if is_abai_failure(s)
        and (
            s.since >= inp.t0 - timedelta(hours=config.ABAI_LOOKBACK_HOURS)
            or s.covers(inp.t0)
        )
    ]
    if failures:
        evidence["abai"] = [
            {
                "since": _iso(s.since),
                "until": _iso(s.until),
                "status": s.code,
                "reason": s.reason,
            }
            for s in sorted(failures, key=lambda s: s.since)
        ]
    return evidence


def _evaluate(
    inp: VerificationInput,
    now: datetime,
    moment: _MomentCheck | None,
) -> VerificationResult:
    wait_until = inp.t0 + timedelta(days=config.PRS_WAIT_DAYS)
    repair = first_repair(inp)
    started = repair if repair is not None and repair.start <= now else None
    window = {"t0": _iso(inp.t0), "prs_wait_until": _iso(wait_until)}

    # 1. Момент вернулся за 1–6 ч — сигнала не было. Исключение — ПРС с обрывом
    #    начался раньше, чем окно момента закрылось (окно поймало ремонт).
    early_rod_break = (
        started is not None
        and started.is_prs
        and started.rod_break
        and moment is not None
        and started.start <= moment.decided_at
    )
    if moment is not None and moment.recovered and not early_rod_break:
        return _result(
            VERDICT_NOT_CONFIRMED,
            REASON_MOMENT_RECOVERED,
            moment.decided_at,
            {"window": window, "moment": moment.evidence},
        )

    # 2. Первый ремонт решает.
    if started is not None:
        return _repair_verdict(inp, started, now, {"window": window})

    # 3. Нефть в норме при работающем приводе.
    block = _block_at(inp)
    norm, norm_source = oil_norm(inp)
    oil = _oil_check(inp, now, norm, block) if norm is not None else None
    if oil is not None and oil.decided_at is not None:
        m = oil.measurement
        return _result(
            VERDICT_NOT_CONFIRMED,
            REASON_OIL_OK_DRIVE_OK,
            oil.decided_at,
            {
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

    # 4. Окно ПРС истекло.
    if now >= wait_until:
        return _result(
            VERDICT_NOT_CONFIRMED,
            REASON_NO_ROD_BREAK_REPAIR,
            wait_until,
            {"window": window, **_interim(inp, now)},
        )

    # 5. Ждём ПРС.
    return _result(
        VERDICT_PENDING,
        None,
        None,
        {"window": window, "waiting": "prs", **_interim(inp, now)},
    )
