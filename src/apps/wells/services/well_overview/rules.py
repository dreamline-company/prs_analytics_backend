"""Сводка по скважине — чистые правила, без базы.

Строки с цифрами считает код; наблюдения — мягкие сигналы ниже порогов
детекторов: за чем следить у скважины без аварий. Тренды — по медианам:
в замерах ЦИТС бывают выбросы (нефть в 2–4 раза выше соседних замеров), и по
последнему замеру сводка прыгала бы. Сводка не спорит с итоговым состоянием
скважины: «привод работает» и «идёт ремонт» пишутся, только когда это же
говорит состояние.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from apps.compensation.constants import (
    RECOMMENDATION_ACCEPTED,
    RECOMMENDATION_APPLIED,
    RECOMMENDATION_PENDING,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)
from apps.detectors.models.verification import VERDICT_FALSE_ALARM
from apps.repairs.services.mrp import MERGE_GAP, RepairSpan, compute_well_mrp
from apps.telemetry.services.well_rates import FRESH_TELEMETRY_DAYS, water_cut
from apps.wells.dto.internal.well_overview import (
    MARK_ATTENTION,
    MARK_INFO,
    MARK_OK,
    WellOverviewItemDTO,
    WellOverviewObservationDTO,
)
from apps.wells.dto.internal.well_state import WellStateDTO
from apps.wells.models.well_state import STATE_INACTIVE, STATE_REPAIR, STATE_WORKING

# Окна.
TREND_WINDOW = timedelta(days=30)  # месяц к предыдущему месяцу
MEASUREMENTS_WINDOW = 2 * TREND_WINDOW
TREND_MIN_MEASUREMENTS = 3  # меньше замеров в окне — тренд не считается
DRIVE_WEEK_DAYS = 7  # момент: последняя неделя к предыдущей, полные сутки
DRIVE_WINDOW_DAYS = 2 * DRIVE_WEEK_DAYS
DRIVE_MIN_DAYS_PER_WEEK = 3
# Сутки, когда привод вращался меньше этой доли отсчётов, в момент не идут:
# медиана по стоянке — не нагрузка насоса.
DRIVE_MIN_ROTATING_SHARE = 0.9
INCIDENTS_WINDOW_DAYS = 90
RECENT_REPAIRS = 8  # частая причина — среди стольких последних ремонтов
# Поиск начала состояния привода по истории не глубже 30 суток: «с» на
# этой границе значит «не меньше 30 суток».
DRIVE_SINCE_HORIZON = timedelta(days=29, hours=12)

# Пороги наблюдений (решение 29.10.2026, «пока так»).
WATER_CUT_RISE_PP = 5.0  # обводнённость выросла за месяц, п.п.
OIL_DROP_SHARE = 0.15  # нефть упала за месяц
MOMENT_RISE_SHARE = 0.10  # момент вырос неделя к неделе
PLAN_OUTDATED_RATIO = 2.0  # факт выше плана во столько раз
PLAN_BELOW_SHARE = 0.85  # последний замер ниже плана больше чем на 15 %
DONOR_SPEED_MISMATCH = 0.05  # скорость в пуле доноров против фактической
FREQUENT_REPAIR_MIN = 3  # одинаковых ремонтов среди последних
MOMENT_FLAT_SHARE = 0.03  # меньше — «без заметных изменений»

PAIR_STATUS_RU = {
    RECOMMENDATION_PENDING: "на согласовании",
    RECOMMENDATION_ACCEPTED: "согласован",
    RECOMMENDATION_APPLIED: "применён",
}
RISK_RU = {RISK_LOW: "низкий", RISK_MEDIUM: "средний", RISK_HIGH: "высокий"}
SEVERITY_MARK = {"ok": MARK_OK, "warning": MARK_ATTENTION, "critical": MARK_ATTENTION}


@dataclass(frozen=True, slots=True)
class Measurement:
    time: datetime
    oil: float | None
    liquid: float | None


@dataclass(frozen=True, slots=True)
class DriveDay:
    """Медианы привода за полные сутки, когда он почти всё время вращался."""

    day: date
    moment: float | None


@dataclass(frozen=True, slots=True)
class RepairFact:
    start: datetime
    end: datetime | None
    type_name: str | None


@dataclass(frozen=True, slots=True)
class IncidentFact:
    title: str
    level: str
    opened_at: datetime
    active: bool
    verdict: str | None  # отметка проверки R2


@dataclass(frozen=True, slots=True)
class DonorFact:
    speed: float  # скорость в пуле доноров
    step_percent: int
    gain: float
    risk: str
    loss_well_name: str | None = None  # открытая пара
    pair_status: str | None = None
    speed_to: float | None = None  # скорость после разгона по паре


@dataclass(frozen=True, slots=True)
class DynamogramFact:
    time: datetime
    condition: str | None  # название состояния на экране («Норма», «Влияние газа»)
    severity: str | None  # ok / warning / critical / unknown
    fill_pct: float | None


@dataclass(frozen=True, slots=True)
class OverviewInputs:
    now: datetime
    state: WellStateDTO | None = None
    # Привод — только если его состояние свежее и подтверждено (как в
    # итоговом состоянии); иначе None.
    drive_running: bool | None = None
    drive_since: datetime | None = None
    # Скорость по последнему отсчёту; None — нет отсчёта или не приведена
    # к единицам (у станции нет типа — сырое значение сравнивать нельзя).
    speed: float | None = None
    speed_units: str | None = None
    drive_days: list[DriveDay] = field(default_factory=list)
    plan_oil: float | None = None
    plan_liquid: float | None = None
    measurements: list[Measurement] = field(default_factory=list)
    dynamic_level: tuple[float, date] | None = None
    repairs: list[RepairFact] = field(default_factory=list)
    incidents: list[IncidentFact] = field(default_factory=list)
    donor: DonorFact | None = None
    dynamogram: DynamogramFact | None = None


@dataclass(slots=True)
class _Overview:
    items: list[WellOverviewItemDTO] = field(default_factory=list)
    observations: list[WellOverviewObservationDTO] = field(default_factory=list)

    def item(
        self,
        code: str,
        title: str,
        text: str,
        mark: str,
        as_of: datetime | date | None = None,
    ) -> None:
        self.items.append(
            WellOverviewItemDTO(
                code=code,
                title_ru=title,
                text_ru=text,
                mark=mark,
                as_of=as_of,
            ),
        )

    def observe(self, code: str, text: str) -> None:
        self.observations.append(WellOverviewObservationDTO(code=code, text_ru=text))


def _num(value: float, digits: int = 1) -> str:
    """Число для текста: запятая, без хвостовых нулей, минус — «−»."""
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", "0"):
        return "0"
    return text.replace(".", ",").replace("-", "−")


def _pct(share: float) -> str:
    rounded = round(share * 100)
    if rounded == 0:
        return "0 %"
    return f"{rounded:+d} %".replace("-", "−")


def _date(moment: datetime | date, now: datetime) -> str:
    """Дата для текста; год — только если не текущий."""
    if moment.year == now.year:
        return moment.strftime("%d.%m")
    return moment.strftime("%d.%m.%Y")


def build_overview(
    inputs: OverviewInputs,
) -> tuple[list[WellOverviewItemDTO], list[WellOverviewObservationDTO]]:
    overview = _Overview()
    _work(inputs, overview)
    _production(inputs, overview)
    _trend(inputs, overview)
    _drive(inputs, overview)
    _dynamogram(inputs, overview)
    _gdis(inputs, overview)
    _repairs(inputs, overview)
    _detectors(inputs, overview)
    _compensation(inputs, overview)
    return overview.items, overview.observations


def _is_working(inputs: OverviewInputs) -> bool:
    return inputs.state is not None and inputs.state.code == STATE_WORKING


def _speed_text(inputs: OverviewInputs) -> str:
    if inputs.speed is None:
        return ""
    units = f" {inputs.speed_units}" if inputs.speed_units else ""
    return f"; {_num(inputs.speed)}{units}"


# --- Блоки ---


def _work(inputs: OverviewInputs, overview: _Overview) -> None:
    state = inputs.state
    if state is None:
        overview.item("work", "Работа", "Состояние ещё не посчитано", MARK_INFO)
        return
    if _is_working(inputs) and inputs.drive_running and inputs.drive_since:
        if inputs.now - inputs.drive_since >= DRIVE_SINCE_HORIZON:
            text = "Привод работает без остановок не меньше 30 суток"
        else:
            text = (
                f"Привод работает с {_date(inputs.drive_since, inputs.now)} "
                f"{inputs.drive_since:%H:%M}"
            )
        text += _speed_text(inputs)
    else:
        text = state.reason_ru
    if state.code == STATE_WORKING:
        mark = MARK_OK
    elif state.code == STATE_INACTIVE:
        mark = MARK_INFO
    else:
        mark = MARK_ATTENTION
    overview.item("work", "Работа", text, mark)


def _deduplicated(measurements: list[Measurement]) -> list[Measurement]:
    """В telemetry_well бывают дубли замера — по одному на минуту."""
    by_minute = {
        item.time.replace(second=0, microsecond=0): item for item in measurements
    }
    return [by_minute[moment] for moment in sorted(by_minute)]


def _production(inputs: OverviewInputs, overview: _Overview) -> None:
    measurements = _deduplicated(inputs.measurements)
    if not measurements:
        days = MEASUREMENTS_WINDOW.days
        overview.item(
            "production",
            "Добыча",
            f"Замеров ЦИТС за {days} суток нет",
            MARK_ATTENTION,
        )
        return
    last = measurements[-1]
    parts = []
    if last.oil is not None:
        part = f"Нефть {_num(last.oil)} т/сут"
        if inputs.plan_oil:
            part += f" при плане {_num(inputs.plan_oil)}"
        parts.append(part)
    if last.liquid is not None:
        part = f"жидкость {_num(last.liquid)} м³/сут"
        if inputs.plan_liquid:
            part += f" при плане {_num(inputs.plan_liquid)}"
        parts.append(part)
    cut = water_cut(liquid_rate=last.liquid, oil_rate=last.oil)
    if cut is not None:
        parts.append(f"обводнённость {_num(cut, 0)} %")
    text = "; ".join(parts) or "В замере нет дебитов"
    text = text[0].upper() + text[1:] + f". Замер {_date(last.time, inputs.now)}"
    stale = inputs.now - last.time > timedelta(days=FRESH_TELEMETRY_DAYS)
    below = (
        bool(inputs.plan_oil)
        and last.oil is not None
        and last.oil < inputs.plan_oil * PLAN_BELOW_SHARE
    )
    mark = MARK_ATTENTION if stale or below else MARK_OK
    overview.item("production", "Добыча", text, mark, last.time)
    if stale:
        overview.observe(
            "measurement_stale",
            f"Замер дебита старше {FRESH_TELEMETRY_DAYS} суток "
            f"(последний {_date(last.time, inputs.now)})",
        )


def _window_medians(
    measurements: list[Measurement],
) -> tuple[float, float] | None:
    oils = [item.oil for item in measurements if item.oil is not None]
    liquids = [item.liquid for item in measurements if item.liquid is not None]
    if len(oils) < TREND_MIN_MEASUREMENTS or len(liquids) < TREND_MIN_MEASUREMENTS:
        return None
    return median(oils), median(liquids)


def _trend(inputs: OverviewInputs, overview: _Overview) -> None:
    """Обводнённость — из медиан нефти и жидкости, а не медианой обводнённостей
    замеров: так она согласована с показанными медианами и тоже не боится
    выбросов."""
    measurements = _deduplicated(inputs.measurements)
    recent_from = inputs.now - TREND_WINDOW
    previous_from = recent_from - TREND_WINDOW
    recent = _window_medians([m for m in measurements if m.time >= recent_from])
    previous = _window_medians(
        [m for m in measurements if previous_from <= m.time < recent_from],
    )
    if recent is None or previous is None:
        return
    (oil, liquid), (oil_before, liquid_before) = recent, previous
    cut = water_cut(liquid_rate=liquid, oil_rate=oil)
    cut_before = water_cut(liquid_rate=liquid_before, oil_rate=oil_before)
    oil_part = f"Нефть {_num(oil)} т/сут против {_num(oil_before)} месяцем раньше"
    oil_change = oil / oil_before - 1 if oil_before > 0 else None
    if oil_change is not None:
        oil_part += f" ({_pct(oil_change)})"
    liquid_part = f"жидкость {_num(liquid)} против {_num(liquid_before)}"
    if liquid_before > 0:
        liquid_part += f" ({_pct(liquid / liquid_before - 1)})"
    parts = [oil_part, liquid_part]
    if cut is not None and cut_before is not None:
        parts.append(f"обводнённость {_num(cut_before, 0)} % → {_num(cut, 0)} %")
    falling = oil_change is not None and oil_change <= -OIL_DROP_SHARE
    watering = (
        cut is not None
        and cut_before is not None
        and cut - cut_before >= WATER_CUT_RISE_PP
    )
    overview.item(
        "production_trend",
        "Добыча за месяц",
        "; ".join(parts) + " (медианы замеров)",
        MARK_ATTENTION if falling or watering else MARK_OK,
    )
    if watering:
        overview.observe(
            "water_cut_rising",
            f"Растёт обводнённость: за месяц с {_num(cut_before, 0)} до "
            f"{_num(cut, 0)} %",
        )
    if falling:
        overview.observe(
            "oil_falling",
            f"Нефть за месяц упала на {_num(-oil_change * 100, 0)} %: "
            f"{_num(oil_before)} → {_num(oil)} т/сут",
        )
    if inputs.plan_oil and oil >= inputs.plan_oil * PLAN_OUTDATED_RATIO:
        overview.observe(
            "plan_outdated",
            f"План по нефти {_num(inputs.plan_oil)} т/сут намного ниже факта "
            f"{_num(oil)} т/сут — похоже, техрежим устарел",
        )


def _drive(inputs: OverviewInputs, overview: _Overview) -> None:
    """Момент — последняя календарная неделя к предыдущей, по полным суткам
    работы привода."""
    today = inputs.now.date()
    week_start = today - timedelta(days=DRIVE_WEEK_DAYS)
    days = [day for day in inputs.drive_days if day.moment is not None]
    last_week = [day.moment for day in days if week_start <= day.day < today]
    week_before = [day.moment for day in days if day.day < week_start]
    if (
        len(last_week) < DRIVE_MIN_DAYS_PER_WEEK
        or len(week_before) < DRIVE_MIN_DAYS_PER_WEEK
    ):
        return
    first, second = median(week_before), median(last_week)
    if first <= 0:
        return
    moments = week_before + last_week
    change = second / first - 1
    text = (
        f"Момент {_num(min(moments), 0)}–{_num(max(moments), 0)} "
        f"за {DRIVE_WINDOW_DAYS} суток"
    )
    if abs(change) < MOMENT_FLAT_SHARE:
        text += ", без заметных изменений"
    else:
        text += f", неделя к неделе {_pct(change)}"
    rising = change >= MOMENT_RISE_SHARE
    overview.item("drive", "Привод", text, MARK_ATTENTION if rising else MARK_OK)
    if rising:
        overview.observe(
            "moment_rising",
            f"Момент растёт: {_pct(change)} за неделю "
            f"({_num(first, 0)} → {_num(second, 0)})",
        )


def _dynamogram(inputs: OverviewInputs, overview: _Overview) -> None:
    fact = inputs.dynamogram
    if fact is None:
        return
    text = f"Последний ИИ-разбор: {fact.condition or 'без оценки'}"
    if fact.fill_pct is not None:
        text += f", наполнение ~{_num(fact.fill_pct, 0)} %"
    text += f", {_date(fact.time, inputs.now)}"
    mark = SEVERITY_MARK.get(fact.severity or "", MARK_INFO)
    overview.item("dynamogram", "Динамограмма", text, mark, fact.time)


def _gdis(inputs: OverviewInputs, overview: _Overview) -> None:
    if inputs.dynamic_level is None:
        return
    level, measured = inputs.dynamic_level
    overview.item(
        "gdis",
        "ГДИС",
        f"Динамический уровень {_num(level, 0)} м на {_date(measured, inputs.now)}",
        MARK_INFO,
        measured,
    )


def _repair_groups(repairs: list[RepairFact]) -> list[RepairFact]:
    """Первая запись каждого ремонта: записи встык или внахлёст (разрыв
    меньше суток) — один ремонт, как в расчёте МРП."""
    groups: list[RepairFact] = []
    end: datetime | None = None
    for repair in repairs:
        if groups and end is not None and repair.start - end < MERGE_GAP:
            end = None if repair.end is None else max(end, repair.end)
            continue
        groups.append(repair)
        end = repair.end
    return groups


def _repairs(inputs: OverviewInputs, overview: _Overview) -> None:
    now = inputs.now
    repairs = sorted(
        (item for item in inputs.repairs if item.start <= now),
        key=lambda item: item.start,
    )
    if not repairs:
        overview.item("repairs", "Ремонты", "Ремонтов в истории нет", MARK_INFO)
        return
    mrp = compute_well_mrp(
        [
            RepairSpan(id=index, start=item.start, end=item.end)
            for index, item in enumerate(repairs)
        ],
        now=now,
    )
    last = repairs[-1]
    last_mrp = mrp.repairs[-1]
    name = last.type_name or "вид работ не указан"
    if mrp.last_end is None:
        if inputs.state is not None and inputs.state.code == STATE_REPAIR:
            overview.item(
                "repairs",
                "Ремонты",
                f"Идёт ремонт: {name}, с {_date(last.start, now)}",
                MARK_ATTENTION,
                last.start,
            )
        else:
            # Ремонт открыт в ABAI, но состояние говорит, что он кончился
            # (после него уже был статус работы) — висящая запись.
            overview.item(
                "repairs",
                "Ремонты",
                f"Ремонт «{name}» с {_date(last.start, now)} не закрыт в ABAI",
                MARK_INFO,
                last.start,
            )
    else:
        text = f"{int(last_mrp.running_days or 0)} сут без ремонта"
        if last_mrp.before_days is not None:
            text += f"; прошлый межремонтный период — {int(last_mrp.before_days)} сут"
        end = mrp.last_end
        text += f". Последний ремонт — {name}, {_date(last.start, now)}–{end:%d.%m.%Y}"
        overview.item("repairs", "Ремонты", text, MARK_INFO, end)

    recent = [
        item.type_name
        for item in _repair_groups(repairs)[-RECENT_REPAIRS:]
        if item.type_name
    ]
    if recent:
        frequent, count = Counter(recent).most_common(1)[0]
        if count >= FREQUENT_REPAIR_MIN:
            overview.observe(
                "frequent_repair",
                f"Частый ремонт: «{frequent}» — {count} из {len(recent)} последних",
            )


def _detectors(inputs: OverviewInputs, overview: _Overview) -> None:
    active = [item for item in inputs.incidents if item.active]
    if active:
        titles = ", ".join(sorted({item.title for item in active}))
        overview.item("detectors", "Детекторы", f"Активно: {titles}", MARK_ATTENTION)
        return
    count = len(inputs.incidents)
    if count == 0:
        overview.item(
            "detectors",
            "Детекторы",
            f"Эпизодов за {INCIDENTS_WINDOW_DAYS} суток нет",
            MARK_OK,
        )
        return
    false_alarms = sum(
        1 for item in inputs.incidents if item.verdict == VERDICT_FALSE_ALARM
    )
    text = f"За {INCIDENTS_WINDOW_DAYS} суток эпизодов: {count}, все закрыты"
    if false_alarms:
        text += f"; ложных по проверке: {false_alarms}"
    overview.item("detectors", "Детекторы", text, MARK_INFO)


def _compensation(inputs: OverviewInputs, overview: _Overview) -> None:
    donor = inputs.donor
    if donor is None:
        return
    gain = (
        f"разгон на {donor.step_percent} % даст +{_num(donor.gain, 2)} т/сут нефти, "
        f"риск {RISK_RU.get(donor.risk, donor.risk)}"
    )
    if not _is_working(inputs):
        state = inputs.state.title_ru.lower() if inputs.state else "нет состояния"
        overview.item(
            "compensation",
            "Контур компенсации",
            f"В пуле доноров, но сейчас не может быть донором: {state}",
            MARK_INFO,
        )
        return
    if donor.loss_well_name is not None:
        status = PAIR_STATUS_RU.get(donor.pair_status or "", donor.pair_status or "")
        text = f"Донор для {donor.loss_well_name} ({status}): {gain}"
    else:
        text = f"В пуле доноров, свободен: {gain}"
    # После применения пары привод уже разогнан — сверять с целевой скоростью.
    expected = (
        donor.speed_to
        if donor.pair_status == RECOMMENDATION_APPLIED and donor.speed_to
        else donor.speed
    )
    stale = (
        inputs.speed is not None
        and inputs.speed > 0
        and expected > 0
        and abs(inputs.speed - expected) / expected > DONOR_SPEED_MISMATCH
    )
    overview.item(
        "compensation",
        "Контур компенсации",
        text,
        MARK_ATTENTION if stale else MARK_INFO,
    )
    if stale:
        units = f" {inputs.speed_units}" if inputs.speed_units else ""
        overview.observe(
            "donor_pool_stale",
            f"В пуле доноров скорость привода {_num(expected)}{units}, "
            f"фактически {_num(inputs.speed)} — рекомендацию разгона нужно "
            "пересчитать",
        )
