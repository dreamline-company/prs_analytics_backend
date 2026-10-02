"""ИИ-заключение языком промысла: факты о насосе вместо механики правила.

Модель получала k, k_alert, k_p90 и писала оператору «коэффициент k 0.2548
превышает k_alert 0.2». Улики взяты из эпизодов прода (UAZ_0032, UVK_0441).
"""

import re
from dataclasses import replace

from apps.detectors.conclusion.facts import facts_for, readable_summary
from apps.detectors.conclusion.summary import (
    ConclusionSummaryInput,
    ConclusionSummaryProcessor,
)

R9_DAYS = [
    ("2026-06-19", 0.2879, -812.0, 2820.0, "alert"),
    ("2026-06-20", 0.2513, -723.5, 2879.5, "grey"),
    ("2026-06-21", 0.2548, -737.0, 2893.0, "alert"),
    ("2026-06-22", 0.248, -708.0, 2855.0, "grey"),
    ("2026-06-23", 0.2562, -727.1, 2838.0, "alert"),
    ("2026-06-24", 0.2613, -737.0, 2820.0, "alert"),
    ("2026-06-25", 0.2771, -806.3, 2910.0, "alert"),
    ("2026-06-26", 0.279, -812.0, 2910.0, "alert"),
    ("2026-06-27", 0.2879, -812.0, 2820.0, "alert"),
    ("2026-06-28", 0.2548, -737.0, 2893.0, "alert"),
]
R9_PAYLOAD = {
    "k": 0.2548,
    "k_alert": 0.2,
    "k_degrade": 0.13,
    "branches": ["rel"],
    "baseline": {
        "k_p90": 0.1696,
        "n_days": 43,
        "p95_median": 3125.0,
        "abs_branch_muted": True,
    },
    "evidence": [
        {"day": d, "k": k, "p5": p5, "p95": p95, "state": state, "n": 700}
        for d, k, p5, p95, state in R9_DAYS
    ],
}
R2_PAYLOAD = {
    "base_moment": 117.0,
    "current_ratio": 0.0,
    "warn_threshold": 0.6,
    "alarm_threshold": 0.4,
    "evidence": [
        {"bucket": f"2026-10-02T{t}:00", "mom_min": m, "mom_med24": 113.0, "spd": 160.0}
        for t, m in [
            ("13:45", 111.0),
            ("14:00", 24.0),
            ("14:15", 14.0),
            ("14:30", 28.0),
            ("14:45", 15.0),
            ("15:00", 110.0),
            ("15:15", 113.0),
            ("15:30", 0.0),
        ]
    ],
}
ITEM = ConclusionSummaryInput(
    cause="Насос поднимает меньше жидкости",
    detector_code="R9",
    level="alarm",
    well_name="UAZ_0032",
    opened_at="2026-06-07T00:00:00",
    last_seen_at="2026-06-29T00:00:00",
    payload=R9_PAYLOAD,
    oil_rate=None,
    liquid_rate=12.4,
    water_cut=None,
    plan_oil_rate=None,
    plan_liquid_rate=15.0,
)


def test_r9_facts_speak_of_strokes_not_coefficients() -> None:
    facts = facts_for(ITEM)

    assert facts == [
        "На ходе вверх, когда штанги поднимают столб жидкости, привод тянет "
        "на 7 % слабее обычного для этой скважины.",
        "На ходе вниз противовесы раскручивают привод: обратный момент сейчас "
        "25 % от рабочего, обычно у этой скважины до 17 %.",
        "Станок и раньше был уравновешен неидеально, тревожит рост перекоса "
        "в 1,5 раза.",
        "Отклонение держится с 19.06.2026: 8 из последних 10 суток.",
        "Последние данные станции — за 28.06.2026.",
        "Дебит жидкости по последнему замеру — 12,4 м³/сут при плане 15,0.",
    ]
    assert not any(re.search(r"[A-Za-z]", fact) for fact in facts)


def test_r9_total_load_loss() -> None:
    payload = {
        **R9_PAYLOAD,
        "evidence": [{**R9_PAYLOAD["evidence"][-1], "p95": 200.0}],
    }

    facts = facts_for(replace(ITEM, payload=payload))

    assert facts[0].startswith("Рабочий момент привода упал почти до нуля — 6 %")


def test_r2_facts_describe_rods_turning_without_load() -> None:
    facts = facts_for(replace(ITEM, detector_code="R2", payload=R2_PAYLOAD))

    assert facts[:4] == [
        "Момент на штангах провалился до 0 % от обычного для этой скважины.",
        "Привод при этом продолжает вращаться: он крутит штанги без нагрузки "
        "насоса — так выглядит обрыв или отворот штанг.",
        "Провал момента — с 02.10 14:00 по 02.10 15:30.",
        "Между провалами момент возвращался к обычному уровню.",
    ]


def test_technical_text_is_replaced_by_facts() -> None:
    facts = facts_for(ITEM)
    technical = "Правило сработало: k 0.2548 превышает k_alert 0.2."

    assert readable_summary(technical, facts, "UAZ_0032") == " ".join(facts[:4])
    assert readable_summary("Порог превышен.", facts, None) == " ".join(facts[:4])


def test_plain_text_with_well_name_is_kept() -> None:
    plain = "На скважине UAZ_0032 насос поднимает меньше жидкости."

    assert readable_summary(plain, facts_for(ITEM), "UAZ_0032") == plain


def test_prompt_carries_facts_not_rule_internals() -> None:
    processor = ConclusionSummaryProcessor(agent=None)  # type: ignore[arg-type]
    state = processor._build_state(ITEM)  # noqa: SLF001
    prompt = state["messages"][0].content

    assert "на 7 % слабее обычного" in prompt
    assert "k_p90" not in prompt
    assert "k_alert" not in prompt
    assert "branches" not in prompt
