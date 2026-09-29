"""Справочник ИИ-заключений: причины, рекомендации, расчёт уверенности.

Всё детерминированное живёт здесь, LLM получает только summary. Формулировки
рекомендаций — рабочий черновик до утверждения технологом: правки текста не
требуют миграций и не ломают историю (заключения хранят копию на момент
генерации).
"""

from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)

# Правила, по которым генерятся заключения. Новый детектор = записи в трёх
# словарях ниже; эпизоды прочих правил задача генерации молча пропускает.
CONCLUSION_DETECTOR_CODES = ("R2", "R9")

# Причина по коду сигнала: код выводится прямо в строке — фронт показывает её
# как есть, а машинные detector_code/reason_code лежат в соседних полях.
CAUSES: dict[tuple[str, str], str] = {
    ("R2", "rod_break"): "Обрыв штанг (R2 / rod_break)",
    ("R9", "load_imbalance"): (
        "Потеря полезной нагрузки на ходе — утечка / перекос (R9 / load_imbalance)"
    ),
    # Для ведомости: заключения по R10 не генерятся (нет в CONCLUSION_DETECTOR_CODES).
    ("R10", "liquid_loss"): "Снижение дебита жидкости по замерам (R10 / liquid_loss)",
}

# Приоритеты шагов: машинный код + подпись для UI.
PRIORITY_CRITICAL = {"priority": "critical", "priority_ru": "Критично"}
PRIORITY_IMPORTANT = {"priority": "important", "priority_ru": "Важно"}
PRIORITY_PLANNED = {"priority": "planned", "priority_ru": "Плановое"}


def _step(
    step: int,
    text: str,
    role: str,
    deadline_hours: int,
    priority: dict,
) -> dict:
    return {
        "step": step,
        "text": text,
        "role": role,
        "deadline_hours": deadline_hours,
        **priority,
    }


RECOMMENDATIONS: dict[tuple[str, str], list[dict]] = {
    ("R2", INCIDENT_LEVEL_WARNING): [
        _step(
            1,
            "Проверить динамограмму и тренд момента за последние сутки",
            "Технолог ЦДНГ",
            24,
            PRIORITY_IMPORTANT,
        ),
        _step(
            2,
            "Сверить скорость и ток привода с режимной картой",
            "Оператор ДНГ",
            24,
            PRIORITY_PLANNED,
        ),
    ],
    ("R2", INCIDENT_LEVEL_ALARM): [
        _step(
            1,
            "Остановить установку и подтвердить обрыв динамометрированием",
            "Мастер ЦДНГ",
            4,
            PRIORITY_CRITICAL,
        ),
        _step(
            2,
            "Оформить заявку на ПРС — подъём и ревизия штанговой колонны",
            "Технолог ЦДНГ",
            24,
            PRIORITY_IMPORTANT,
        ),
        _step(
            3,
            "Проанализировать историю нагрузок до события (перегрузы, заклинивания)",
            "Технолог ЦДНГ",
            72,
            PRIORITY_PLANNED,
        ),
    ],
    ("R9", INCIDENT_LEVEL_WARNING): [
        _step(
            1,
            "Проверить динамограмму на утечку клапанов и неполное заполнение",
            "Технолог ЦДНГ",
            48,
            PRIORITY_IMPORTANT,
        ),
        _step(
            2,
            "Сверить дебит по ГЗУ с планом техрежима",
            "Оператор ДНГ",
            24,
            PRIORITY_PLANNED,
        ),
    ],
    ("R9", INCIDENT_LEVEL_ALARM): [
        _step(
            1,
            "Внеочередное динамометрирование — подтвердить потерю полезной нагрузки",
            "Мастер ЦДНГ",
            24,
            PRIORITY_CRITICAL,
        ),
        _step(
            2,
            "Проверить клапаны и посадку плунжера — типовая картина утечки",
            "Технолог ЦДНГ",
            48,
            PRIORITY_IMPORTANT,
        ),
        _step(
            3,
            "При подтверждении — оценить кандидатуру на ревизию насоса / ПРС",
            "Технолог ЦДНГ",
            72,
            PRIORITY_PLANNED,
        ),
    ],
}


def cause_for(detector_code: str, reason_code: str) -> str | None:
    return CAUSES.get((detector_code, reason_code))


def recommendations_for(detector_code: str, level: str) -> list[dict]:
    return RECOMMENDATIONS.get((detector_code, level), [])


def _clamp(value: float, lo: float = 0.2, hi: float = 0.95) -> float:
    return round(min(max(value, lo), hi), 2)


def _r2_confidence(payload: dict) -> float:
    """R2: доля подтверждающих корзин + глубина провала − штраф слабой базы.

    Подтверждающая корзина — момент ниже warning-порога от суточной медианы.
    Глубина ниже alarm-порога добавляет уверенности, флаг low_confidence
    (base_moment меньше минимума правила) — снимает.
    """
    evidence = payload.get("evidence") or []
    warn = payload.get("warn_threshold")
    confirming = 0
    rated = 0
    for bucket in evidence:
        mom_min, mom_med = bucket.get("mom_min"), bucket.get("mom_med24")
        if mom_min is None or not mom_med or warn is None:
            continue
        rated += 1
        if mom_min / mom_med <= warn:
            confirming += 1
    fraction = confirming / rated if rated else 0.0

    score = 0.4 + 0.4 * fraction
    current = payload.get("current_ratio")
    alarm = payload.get("alarm_threshold")
    if current is not None and alarm is not None and current <= alarm:
        score += 0.1
    if payload.get("low_confidence"):
        score -= 0.15
    return _clamp(score)


def _r9_confidence(payload: dict) -> float:
    """R9: доля alert-суток в уликах + число веток + зрелость базы."""
    evidence = payload.get("evidence") or []
    alert_days = sum(1 for day in evidence if day.get("state") == "alert")
    fraction = alert_days / len(evidence) if evidence else 0.0

    branches = payload.get("branches") or []
    baseline_days = (payload.get("baseline") or {}).get("n_days") or 0

    score = 0.35 + 0.35 * fraction
    if len(branches) >= 2:  # noqa: PLR2004 — обе ветки (absolute + relative)
        score += 0.1
    score += 0.1 * min(baseline_days / 45, 1.0)
    return _clamp(score)


def compute_confidence(detector_code: str, payload: dict | None) -> float:
    """0..1 из улик payload — детерминированно, без LLM.

    Нет payload — 0.5: сигнал есть, оценить качество улик нечем.
    """
    if not payload:
        return 0.5
    if detector_code == "R2":
        return _r2_confidence(payload)
    if detector_code == "R9":
        return _r9_confidence(payload)
    return 0.5
