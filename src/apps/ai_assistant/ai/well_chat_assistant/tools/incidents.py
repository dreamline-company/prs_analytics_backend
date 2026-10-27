# ruff: noqa: TC001, TC002
"""Tools по детекторам: эпизоды R2/R9/R10 с отметкой проверки, ИИ-заключение."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    clamp,
    get_well_id,
    iso,
    local_now,
)
from apps.detectors.dto.internal.incident import DetectorIncidentDTO
from apps.detectors.dto.queries.incident import ListIncidentsByWellIdQuery
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorIncidentRepository,
    DetectorRepository,
    DetectorVerificationRepository,
)
from apps.detectors.use_cases.get_well_ai_conclusion import (
    GetWellAiConclusionUseCase,
)
from apps.detectors.use_cases.list_incidents_by_well_id import (
    ListIncidentsByWellIdUseCase,
)
from shared.database.sql.setup import session_makers

INCIDENTS_DEFAULT_DAYS = 90
INCIDENTS_MAX_DAYS = 365
INCIDENTS_LIMIT = 20

# Ключевые цифры улик по правилу — без рядов корзин и суток.
PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "R2": (
        "current_ratio",
        "base_moment",
        "failure_dt",
        "event_class",
        "lead_time_hours",
        "low_confidence",
    ),
    "R9": ("k", "branches"),
    "R10": (
        "event",
        "klass",
        "q_last",
        "rezhim",
        "dev",
        "n_series",
        "series_from",
        "last_day",
        "su",
        "note",
    ),
}

VERDICT_RU = {
    "pending": "ожидает проверки",
    "false_alarm": "ложная тревога",
    "failure_likely": "скорее всего отказ",
    "failure_confirmed": "отказ подтверждён",
    "undetermined": "не удалось проверить",
}


def incident_brief(incident: DetectorIncidentDTO) -> dict[str, Any]:
    """Эпизод в коротком виде: шапка, ключевые цифры и отметка проверки."""
    payload = incident.payload or {}
    keys = PAYLOAD_KEYS.get(incident.detector_code, ())
    brief: dict[str, Any] = {
        "incident_id": incident.id,
        "detector_code": incident.detector_code,
        "detector_name": incident.detector_name_ru,
        "reason_code": incident.reason_code,
        "level": incident.level,
        "status": incident.status,
        "opened_at": iso(incident.opened_at),
        "last_seen_at": iso(incident.last_seen_at),
        "escalated_at": iso(incident.escalated_at),
        "normalized_at": iso(incident.normalized_at),
        "close_reason": incident.close_reason,
        "facts": {key: payload[key] for key in keys if payload.get(key) is not None},
    }
    if incident.payload is not None and incident.detector_code == "R9":
        baseline = payload.get("baseline") or {}
        if baseline.get("k_p90") is not None:
            brief["facts"]["k_background_p90"] = baseline["k_p90"]
    verification = incident.verification
    brief["verification"] = (
        None
        if verification is None
        else {
            "verdict": verification.verdict,
            "verdict_ru": VERDICT_RU.get(verification.verdict),
            "reason": verification.reason,
            "is_final": verification.is_final,
        }
    )
    return brief


async def list_incident_dtos(
    use_case: ListIncidentsByWellIdUseCase,
    *,
    well_id: int,
    days: int,
) -> list[DetectorIncidentDTO]:
    """Активные эпизоды (сколь бы давно ни открылись) плюс открытые за ``days``."""
    active = await use_case.execute(
        ListIncidentsByWellIdQuery(well_id=well_id, status="active"),
    )
    recent = await use_case.execute(
        ListIncidentsByWellIdQuery(
            well_id=well_id,
            opened_from=local_now() - timedelta(days=days),
            limit=INCIDENTS_LIMIT,
        ),
    )
    merged = {incident.id: incident for incident in [*active, *recent]}
    return sorted(merged.values(), key=lambda item: item.opened_at, reverse=True)


@tool
async def get_incidents(
    config: RunnableConfig,
    days: int = INCIDENTS_DEFAULT_DAYS,
) -> dict[str, Any]:
    """Эпизоды детекции по скважине: R2 — обрыв штанг, R9 — перекос нагрузки
    ШГН, R10 — падение дебита по замерам ЦИТС. Активные — все, закрытые —
    открытые за последние ``days`` суток (по умолчанию 90, максимум 365).

    По каждому эпизоду: уровень (warning/alarm), статус (active/normalized),
    даты, причина закрытия, ключевые цифры в ``facts`` и отметка проверки
    тревоги в ``verification`` (пока только R2): verdict_ru — «ложная тревога»,
    «отказ подтверждён» и т. п. Используй для вопросов «были ли тревоги»,
    «подтвердился ли обрыв», «почему закрылся эпизод».
    """
    well_id = get_well_id(config)
    days = clamp(days, 1, INCIDENTS_MAX_DAYS)
    async with session_makers["app"]() as session:
        use_case = ListIncidentsByWellIdUseCase(
            incident_repository=DetectorIncidentRepository(session),
            detector_repository=DetectorRepository(session),
            verification_repository=DetectorVerificationRepository(session),
        )
        incidents = await list_incident_dtos(use_case, well_id=well_id, days=days)
    return {
        "days": days,
        "total": len(incidents),
        "incidents": [incident_brief(item) for item in incidents[:INCIDENTS_LIMIT]],
    }


@tool
async def get_ai_conclusion(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает актуальное ИИ-заключение по скважине: причина, уверенность,
    рекомендации и summary. primary — эпизод худшего уровня."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        conclusion = await GetWellAiConclusionUseCase(
            incident_repository=DetectorIncidentRepository(session=session),
            conclusion_repository=DetectorConclusionRepository(session=session),
            detector_repository=DetectorRepository(session=session),
        ).execute(well_id)
        return conclusion.model_dump(mode="json")
