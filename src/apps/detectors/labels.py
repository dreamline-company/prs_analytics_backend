"""Подписи эпизодов детекторов для людей: стадия, метки графика, итог проверки.

Одна точка для API, состояния скважины, хронологии, ведомости и ИИ-чата —
чтобы фронт не собирал подписи из кодов. У R2 уровни — стадии сигнала
(«Падение нагрузки» → «Возможный обрыв штанг», правило заказчика 19.11.2026),
у остальных правил — «Предупреждение» / «Авария», как раньше.
"""

from apps.detectors.models.incident import (
    CLOSE_REASON_MANUAL,
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_REPAIR,
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)
from apps.detectors.models.verification import REASON_LABELS_RU, VERDICT_LABELS_RU
from apps.detectors.rod_breaks.incident_config import STAGE_LABELS_RU

DEFAULT_LEVEL_LABELS_RU = {
    INCIDENT_LEVEL_WARNING: "Предупреждение",
    INCIDENT_LEVEL_ALARM: "Авария",
}
# Правила, у которых уровень — стадия сигнала: подпись стадии заменяет имя
# детектора там, где показывают, что происходит со скважиной.
STAGE_LEVEL_LABELS_RU: dict[str, dict[str, str]] = {"R2": STAGE_LABELS_RU}

DEFAULT_CLOSE_LABELS_RU = {
    CLOSE_REASON_RECOVERED: "Норма",
    CLOSE_REASON_REPAIR: "Начался ремонт",
    CLOSE_REASON_STALE: "Закрыт по давности",
    CLOSE_REASON_MANUAL: "Закрыт вручную",
}
CLOSE_LABELS_RU_BY_DETECTOR: dict[str, dict[str, str]] = {
    "R2": {CLOSE_REASON_RECOVERED: "Момент восстановился"},
}


def has_stages(detector_code: str) -> bool:
    return detector_code in STAGE_LEVEL_LABELS_RU


def level_label_ru(detector_code: str, level: str) -> str:
    """Подпись уровня: у R2 — стадия, у прочих — «Предупреждение» / «Авария»."""
    labels = STAGE_LEVEL_LABELS_RU.get(detector_code, DEFAULT_LEVEL_LABELS_RU)
    return labels.get(level) or DEFAULT_LEVEL_LABELS_RU.get(level, level)


def signal_title_ru(
    detector_code: str,
    level: str,
    detector_name_ru: str | None,
) -> str:
    """Что показать о сигнале одной строкой: у R2 — стадия, у прочих — имя
    правила («Перекос нагрузки»)."""
    if has_stages(detector_code):
        return level_label_ru(detector_code, level)
    return detector_name_ru or detector_code


def opened_label_ru(detector_code: str, detector_name_ru: str | None) -> str:
    """Метка начала эпизода на графике."""
    if has_stages(detector_code):
        return level_label_ru(detector_code, INCIDENT_LEVEL_WARNING)
    return detector_name_ru or detector_code


def escalated_label_ru(detector_code: str, detector_name_ru: str | None) -> str:
    """Метка эскалации на графике."""
    if has_stages(detector_code):
        return level_label_ru(detector_code, INCIDENT_LEVEL_ALARM)
    name = detector_name_ru or detector_code
    return f"{name}: {DEFAULT_LEVEL_LABELS_RU[INCIDENT_LEVEL_ALARM].lower()}"


def closed_label_ru(detector_code: str, close_reason: str | None) -> str:
    """Метка закрытия на графике — по причине закрытия."""
    reason = close_reason or CLOSE_REASON_RECOVERED
    specific = CLOSE_LABELS_RU_BY_DETECTOR.get(detector_code, {})
    return specific.get(reason) or DEFAULT_CLOSE_LABELS_RU.get(reason, reason)


def verdict_label_ru(verdict: str | None) -> str | None:
    return VERDICT_LABELS_RU.get(verdict, verdict) if verdict else None


def reason_label_ru(reason: str | None) -> str | None:
    return REASON_LABELS_RU.get(reason, reason) if reason else None


def outcome_text_ru(verdict: str | None, reason: str | None) -> str | None:
    """«Подтверждено: ПРС с обрывом штанг», «Ожидает подтверждения»."""
    label = verdict_label_ru(verdict)
    if label is None:
        return None
    reason_label = reason_label_ru(reason)
    return f"{label}: {lower_first(reason_label)}" if reason_label else label


def lower_first(text: str) -> str:
    """Первая буква строчная, кроме аббревиатур («ПРС с обрывом»)."""
    if not text or (len(text) > 1 and text[1].isupper()):
        return text
    return text[0].lower() + text[1:]
