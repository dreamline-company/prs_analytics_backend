"""Пул доноров по методике подбора (ред. 3) — чистая логика, без базы.

Скважина входит в пул, только если выполнены все критерии сразу (компенсация
одного другим не допускается). Проверено тестом 03.11.2026
(docs/donor_gzu_test_criteria.md):

1. в работе — состояние «работает» или «предупреждение» (авария — нет);
2. способ эксплуатации ШГН или ЭВН;
3. суточный ряд нефти — не меньше 25 из 27 суток;
4. средняя нефть ≥ 2 т/сут (ЭВН) / ≥ 2,5 т/сут (ШГН);
5. стабильность — коэффициент вариации ≤ 0,30;
6. обводнённость ≤ 90 %;
7. погружение (глубина спуска − Ндин) ≥ 100 м; нет данных — не в пуле;
8. межремонтный период ≥ 180 сут; не определён — не в пуле;
9. исправность по телеметрии за 72 ч: ШГН — K = |P5|/P95 момента не выше 70-го
   процентиля по месторождению; ЭВН — разброс момента ≤ 0,06 и дрейф ≤ 8 %;
   заполнение насоса (P5) ≥ 70 % ШГН / ≥ 90 % ЭВН.

Прошедшие 1–8 без телеметрии — отдельный перечень (п. 6.4), в подбор не идут.
Ступень — по погружению, прирост = средняя нефть × ступень.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

LIFT_SHGN = "ШГН"
LIFT_EVN = "ЭВН"
OIL_MIN = {LIFT_EVN: 2.0, LIFT_SHGN: 2.5}
FILL_MIN = {LIFT_EVN: 90.0, LIFT_SHGN: 70.0}
SERIES_DAYS = 27
SERIES_MIN_DAYS = 25
CV_MAX = 0.30
CV_FLAG = 0.25
WATER_CUT_MAX = 90.0
WATER_CUT_FLAG = 85.0
SUBMERGENCE_MIN = 100.0
MRP_MIN_DAYS = 180
MOMENT_CV_MAX = 0.06
MOMENT_DRIFT_MAX = 0.08
TELEMETRY_MIN_SAMPLES = 100
K_PERCENTILE = 0.70
PUMP_AGE_PERCENTILE = 0.85
FRESH_REPAIR_DAYS = 30
WORKING_STATES = frozenset({"working", "warning"})

# Критерии в порядке воронки.
CRITERIA = (
    ("state", "в работе"),
    ("lift", "ШГН или ЭВН"),
    ("series", "ряд добычи ≥ 25 из 27 сут"),
    ("oil", "нефть ≥ 2 / 2,5 т/сут"),
    ("cv", "стабильность: CV ≤ 0,30"),
    ("water_cut", "обводнённость ≤ 90 %"),
    ("submergence", "погружение ≥ 100 м"),
    ("mrp", "МРП ≥ 180 сут"),
    ("telemetry", "исправность по телеметрии"),
)


@dataclass(frozen=True, slots=True)
class Telemetry:
    samples: int
    k: float | None  # |P5| / P95 момента
    moment_cv: float | None
    moment_drift: float | None  # |ср. 2-й половины − 1-й| / ср. 1-й
    fill_p5: float | None  # заполнение насоса, %
    speed: float | None  # медианная скорость, об/мин или кач/мин


@dataclass(frozen=True, slots=True)
class PoolInput:
    well_id: int
    name: str
    field: str  # префикс месторождения
    state: str
    lift: str | None
    days: int  # суток с данными из SERIES_DAYS
    oil: float | None  # средняя нефть, т/сут
    cv: float | None
    flat: bool  # ряд не меняется ни в одни сутки
    water_cut: float | None
    pump_depth: float | None
    hdin: float | None
    mrp_days: int | None
    since_repair_days: int | None  # после последнего ПРС
    pump_age_days: int | None  # после последней смены насоса
    telemetry: Telemetry | None


@dataclass(frozen=True, slots=True)
class PoolResult:
    well_id: int
    in_pool: bool  # прошёл всё и есть телеметрия
    has_telemetry: bool
    passed: bool  # прошёл критерии 1–8 (и 9, если есть телеметрия)
    fails: list[tuple[str, str]] = field(default_factory=list)  # (критерий, текст)
    flags: list[str] = field(default_factory=list)
    submergence: float | None = None
    step_percent: int | None = None
    gain: float | None = None


@dataclass(frozen=True, slots=True)
class FieldNorms:
    """Пороги-процентили по месторождению: K у ШГН и наработка насоса."""

    k_max: dict[str, float]
    pump_age_max: dict[str, float]


def percentile(values: Iterable[float], q: float) -> float | None:
    """Линейная интерполяция, как percentile_cont."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def field_norms(inputs: Iterable[PoolInput]) -> FieldNorms:
    """По работающим скважинам месторождения (как «по фонду месторождения»)."""
    k_values: dict[str, list[float]] = defaultdict(list)
    ages: dict[str, list[float]] = defaultdict(list)
    for item in inputs:
        if item.state not in (*WORKING_STATES, "alarm"):
            continue
        tel = item.telemetry
        if item.lift == LIFT_SHGN and tel is not None and tel.k is not None:
            k_values[item.field].append(tel.k)
        if item.pump_age_days is not None:
            ages[item.field].append(item.pump_age_days)
    return FieldNorms(
        k_max={
            f: v
            for f, values in k_values.items()
            if (v := percentile(values, K_PERCENTILE)) is not None
        },
        pump_age_max={
            f: v
            for f, values in ages.items()
            if (v := percentile(values, PUMP_AGE_PERCENTILE)) is not None
        },
    )


# Допустимая ступень разгона по погружению (п. 9.1): (свыше, м; ступень, %).
STEPS = ((300.0, 15), (200.0, 10), (150.0, 7))
MIN_STEP = 5


def step_for(submergence: float) -> int:
    """Допустимая ступень разгона по погружению (п. 9.1)."""
    return next((step for above, step in STEPS if submergence > above), MIN_STEP)


def has_telemetry(item: PoolInput) -> bool:
    return (
        item.telemetry is not None and item.telemetry.samples >= TELEMETRY_MIN_SAMPLES
    )


def _telemetry_fails(item: PoolInput, norms: FieldNorms) -> list[str]:
    tel = item.telemetry
    fails = []
    if item.lift == LIFT_SHGN:
        k_max = norms.k_max.get(item.field)
        if tel.k is not None and k_max is not None and tel.k > k_max:
            fails.append(f"K {tel.k:.2f} > P70 {k_max:.2f}")
    else:
        if tel.moment_cv is not None and tel.moment_cv > MOMENT_CV_MAX:
            fails.append(f"разброс момента {tel.moment_cv:.3f}")
        if tel.moment_drift is not None and tel.moment_drift > MOMENT_DRIFT_MAX:
            fails.append(f"дрейф момента {tel.moment_drift * 100:.0f} %")
    fill_min = FILL_MIN[item.lift]
    if tel.fill_p5 is None or tel.fill_p5 < fill_min:
        fill = f"{tel.fill_p5:.0f} %" if tel.fill_p5 is not None else "нет"
        fails.append(f"заполнение P5 {fill} < {fill_min:.0f} %")
    return fails


def _flags(item: PoolInput, norms: FieldNorms, submergence: float | None) -> list[str]:
    flags = []
    if item.hdin is None or (submergence is not None and submergence < 0):
        flags.append("Ндин не замерен / отриц. погружение")
    if (
        item.since_repair_days is not None
        and item.since_repair_days < FRESH_REPAIR_DAYS
    ):
        flags.append(f"после ПРС {item.since_repair_days} сут")
    age_max = norms.pump_age_max.get(item.field)
    if item.pump_age_days is None:
        flags.append("наработка насоса не определена")
    elif item.pump_age_days < FRESH_REPAIR_DAYS or (
        age_max is not None and item.pump_age_days > age_max
    ):
        flags.append(f"наработка насоса {item.pump_age_days} сут")
    if item.cv is not None and item.cv > CV_FLAG:
        flags.append(f"CV {item.cv:.2f}")
    if item.water_cut is not None and item.water_cut >= WATER_CUT_FLAG:
        flags.append(f"обводнённость {item.water_cut:.0f} %")
    if item.flat:
        flags.append("ряд без изменений")
    return flags


def evaluate(item: PoolInput, norms: FieldNorms) -> PoolResult:  # noqa: C901
    fails: list[tuple[str, str]] = []
    if item.state not in WORKING_STATES:
        fails.append(
            (
                "state",
                "авария по детекторам" if item.state == "alarm" else "не в работе",
            ),
        )
    if item.lift not in OIL_MIN:
        fails.append(("lift", f"способ: {item.lift or 'не указан'}"))
    if item.days < SERIES_MIN_DAYS:
        fails.append(("series", f"данных {item.days} из {SERIES_DAYS} сут"))
    if item.oil is None:
        fails.append(("oil", "нефти нет"))
    elif item.lift in OIL_MIN and item.oil < OIL_MIN[item.lift]:
        fails.append(("oil", f"нефть {item.oil:.2f} т/сут"))
    if item.cv is None or item.cv > CV_MAX:
        fails.append(
            ("cv", f"CV {item.cv:.2f}" if item.cv is not None else "CV не посчитан"),
        )
    if item.water_cut is None or item.water_cut > WATER_CUT_MAX:
        text = (
            f"обводнённость {item.water_cut:.0f} %"
            if item.water_cut is not None
            else "обводнённости нет"
        )
        fails.append(("water_cut", text))
    submergence = (
        item.pump_depth - item.hdin
        if item.pump_depth and item.hdin is not None
        else None
    )
    if submergence is None:
        fails.append(
            (
                "submergence",
                "нет глубины насоса" if not item.pump_depth else "нет Ндин",
            ),
        )
    elif submergence < SUBMERGENCE_MIN:
        fails.append(("submergence", f"погружение {submergence:.0f} м"))
    if item.mrp_days is None:
        fails.append(("mrp", "МРП не определён"))
    elif item.mrp_days < MRP_MIN_DAYS:
        fails.append(("mrp", f"МРП {item.mrp_days} сут"))
    telemetry = has_telemetry(item)
    if telemetry and item.lift in FILL_MIN:
        fails.extend(("telemetry", text) for text in _telemetry_fails(item, norms))
    passed = not fails
    step = step_for(submergence) if passed else None
    return PoolResult(
        well_id=item.well_id,
        in_pool=passed and telemetry,
        has_telemetry=telemetry,
        passed=passed,
        fails=fails,
        flags=_flags(item, norms, submergence),
        submergence=submergence,
        step_percent=step,
        gain=item.oil * step / 100
        if step is not None and item.oil is not None
        else None,
    )


def funnel(
    results: Mapping[int, PoolResult],
) -> list[tuple[str, int]]:
    """Сколько скважин остаётся после каждого критерия (последовательно)."""
    alive = list(results.values())
    steps = [("всего", len(alive))]
    for key, title in CRITERIA:
        alive = [r for r in alive if key not in {k for k, _text in r.fails}]
        steps.append((title, len(alive)))
    steps.append(("в пуле (с телеметрией)", sum(r.in_pool for r in alive)))
    return steps
