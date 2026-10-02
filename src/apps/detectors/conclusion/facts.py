"""Улики эпизода — готовыми фразами о физике насоса для заключения.

Модель получала сырой payload (k, k_p90, branches) и пересказывала пороги
правила — оператору это ничего не говорит. Здесь улики переводятся в факты о
штангах, ходе вверх/вниз и дебите; числа считает код, модель только связывает
фразы. Если её текст всё же технический, вместо него идут сами факты.
"""

import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from apps.detectors.load_imbalance import config as r9_config

if TYPE_CHECKING:
    from apps.detectors.conclusion.summary import ConclusionSummaryInput

# Признаки «айтишного» текста: латиница (кроме имени скважины) и слова о
# механике правила, а не о насосе.
_TECHNICAL = re.compile(
    r"[A-Za-z]|\bкоэффициент\w*|\bпорог\w*|\bправил[оауе]?\b|\bдетекц\w*"
    r"|\bперцентил\w*|\bмедиан\w*|\bалгоритм\w*|\bветв\w*",
    re.IGNORECASE,
)


def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _pct(share: float) -> str:
    return f"{share * 100:.0f} %"


def _day(value: str) -> str:
    return date.fromisoformat(value[:10]).strftime("%d.%m.%Y")


def _r9_facts(payload: dict) -> list[str]:
    days = [d for d in payload.get("evidence") or [] if d.get("p95")]
    if not days:
        return []
    last = days[-1]
    baseline = payload.get("baseline") or {}
    usual_peak = baseline.get("p95_median")
    usual_k = baseline.get("k_p90")
    facts = []
    if usual_peak:
        share = last["p95"] / usual_peak
        if share < r9_config.LOAD_LOSS_FRAC:
            facts.append(
                "Рабочий момент привода упал почти до нуля — "
                f"{_pct(share)} от обычного для скважины: привод качает "
                "станок без нагрузки, насос жидкость не поднимает.",
            )
        else:
            change = abs(share - 1)
            facts.append(
                "На ходе вверх, когда штанги поднимают столб жидкости, привод "
                f"тянет на {_pct(change)} "
                f"{'слабее' if share < 1 else 'сильнее'} обычного для этой "
                "скважины.",
            )
    if last.get("k") is not None:
        usual = f", обычно у этой скважины до {_pct(usual_k)}" if usual_k else ""
        facts.append(
            "На ходе вниз противовесы раскручивают привод: обратный момент "
            f"сейчас {_pct(last['k'])} от рабочего{usual}.",
        )
    if baseline.get("abs_branch_muted") and usual_k and last.get("k"):
        facts.append(
            "Станок и раньше был уравновешен неидеально, тревожит рост "
            f"перекоса в {_num(last['k'] / usual_k)} раза.",
        )
    alert_days = [d for d in days if d.get("state") == "alert"]
    if alert_days:
        facts.append(
            f"Отклонение держится с {_day(alert_days[0]['day'])}: "
            f"{len(alert_days)} из последних {len(days)} суток.",
        )
    facts.append(f"Последние данные станции — за {_day(last['day'])}.")
    return facts


def _r2_facts(payload: dict) -> list[str]:
    buckets = [
        b
        for b in payload.get("evidence") or []
        if b.get("mom_min") is not None and b.get("mom_med24")
    ]
    warn = payload.get("warn_threshold")
    dips = [b for b in buckets if warn is None or b["mom_min"] / b["mom_med24"] <= warn]
    if not dips:
        return []
    deepest = min(b["mom_min"] / b["mom_med24"] for b in dips)
    first = datetime.fromisoformat(dips[0]["bucket"])
    last = datetime.fromisoformat(dips[-1]["bucket"])
    facts = [
        "Момент на штангах провалился до "
        f"{_pct(deepest)} от обычного для этой скважины.",
    ]
    if all(b.get("spd") for b in dips):
        facts.append(
            "Привод при этом продолжает вращаться: он крутит штанги без "
            "нагрузки насоса — так выглядит обрыв или отворот штанг.",
        )
    when = (
        f"{first:%d.%m %H:%M}"
        if first == last
        else f"с {first:%d.%m %H:%M} по {last:%d.%m %H:%M}"
    )
    facts.append(f"Провал момента — {when}.")
    after_first = buckets[buckets.index(dips[0]) :]
    if any(b not in dips for b in after_first):
        facts.append("Между провалами момент возвращался к обычному уровню.")
    if payload.get("low_confidence"):
        facts.append(
            "Обычная нагрузка на штанги у этой скважины мала, поэтому вывод "
            "менее надёжен.",
        )
    return facts


def _rate_facts(item: "ConclusionSummaryInput") -> list[str]:
    facts = []
    for label, fact, plan, unit in (
        ("жидкости", item.liquid_rate, item.plan_liquid_rate, "м³/сут"),
        ("нефти", item.oil_rate, item.plan_oil_rate, "т/сут"),
    ):
        if fact is None:
            continue
        plan_text = f" при плане {_num(plan)}" if plan else ""
        facts.append(
            f"Дебит {label} по последнему замеру — {_num(fact)} {unit}{plan_text}.",
        )
    if item.water_cut is not None:
        facts.append(f"Обводнённость — {item.water_cut:.0f} %.")
    return facts


def facts_for(item: "ConclusionSummaryInput") -> list[str]:
    """Физические факты эпизода в порядке важности."""
    payload = item.payload or {}
    by_detector = {"R2": _r2_facts, "R9": _r9_facts}.get(item.detector_code)
    detector_facts = by_detector(payload) if by_detector else []
    return detector_facts + _rate_facts(item)


def readable_summary(text: str | None, facts: list[str], well_name: str | None) -> str:
    """Текст модели, если он без техники; иначе — сами факты."""
    probe = (text or "").replace(well_name, "") if well_name else (text or "")
    if text and probe.strip() and not _TECHNICAL.search(probe):
        return text.strip()
    return " ".join(facts[:4]) or (text or "").strip()
