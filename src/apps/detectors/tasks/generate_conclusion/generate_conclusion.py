"""Генерация ИИ-заключения по эпизоду R2/R9.

Запускается по событию (раннер открыл/эскалировал эпизод) и страховочно из
подметальщика. Fingerprint — (incident_id, level): максимум два заключения на
эпизод, повторный запуск при готовом completed выходит сразу.

    python -m apps.detectors.tasks.generate_conclusion.generate_conclusion \
        --incident-id 42 [--force]

``--force`` перегенерирует и готовое заключение — после смены промпта.

Причина и рекомендации — из справочника, уверенность — из улик, LLM пишет
только summary; его падение оставляет строку в status=failed для повтора.
"""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.conclusion import catalog
from apps.detectors.conclusion.facts import facts_for, readable_summary
from apps.detectors.conclusion.summary import (
    ConclusionSummaryInput,
    ConclusionSummaryProcessor,
    build_summary_agent,
)
from apps.detectors.dto.internal.repositories.conclusion import CreateConclusionDTO
from apps.detectors.models.conclusion import (
    CONCLUSION_STATUS_COMPLETED,
    CONCLUSION_STATUS_FAILED,
    CONCLUSION_STATUS_PENDING,
)
from apps.detectors.models.incident import DetectorIncident
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorIncidentRepository,
)
from apps.models_registry import *  # noqa: F403
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.well_rates import WellRatesService
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()


class ConclusionGenerator:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.incident_repo = DetectorIncidentRepository(session)
        self.conclusion_repo = DetectorConclusionRepository(session)

    async def run(self, incident_id: int, *, force: bool = False) -> None:
        incident = await self.incident_repo.get_one(
            QuerySpec(filters=(DetectorIncident.id == incident_id,)),
        )
        if incident is None:
            logger.warning("Conclusion: incident id=%s not found", incident_id)
            return
        if incident.detector_code not in catalog.CONCLUSION_DETECTOR_CODES:
            logger.info(
                "Conclusion: detector %s not supported (incident id=%s)",
                incident.detector_code,
                incident_id,
            )
            return
        cause = catalog.cause_for(incident.detector_code, incident.reason_code)
        if cause is None:
            logger.warning(
                "Conclusion: no cause in catalog for (%s, %s)",
                incident.detector_code,
                incident.reason_code,
            )
            return

        existing = await self.conclusion_repo.get_by_incident_and_level(
            incident_id=incident.id,
            level=incident.level,
        )
        if (
            not force
            and existing is not None
            and existing.status == CONCLUSION_STATUS_COMPLETED
        ):
            logger.info(
                "Conclusion for incident id=%s level=%s already completed",
                incident.id,
                incident.level,
            )
            return

        base = CreateConclusionDTO(
            incident_id=incident.id,
            well_id=incident.well_id,
            detector_code=incident.detector_code,
            reason_code=incident.reason_code,
            level=incident.level,
            status=CONCLUSION_STATUS_PENDING,
            cause=cause,
            recommendations=catalog.recommendations_for(
                incident.detector_code,
                incident.level,
            ),
            confidence=catalog.compute_confidence(
                incident.detector_code,
                incident.payload,
            ),
            prompt_version=ConclusionSummaryProcessor.prompt_version,
            model_name=settings.LLM_MODEL_NAME,
        )
        # Резерв строки до похода в LLM: фронт видит «генерится», повтор задачи
        # после сбоя перезапишет по конфликту (incident_id, level).
        await self.conclusion_repo.upsert(base)
        await self.session.commit()

        summary_input = await self._build_summary_input(incident, cause)
        result = await ConclusionSummaryProcessor(
            build_summary_agent(),
            model_name=settings.LLM_MODEL_NAME,
        ).process(summary_input)

        text = (result.result or {}).get("text") if result.result else None
        if result.status == "completed" and text:
            summary = readable_summary(
                text,
                facts_for(summary_input),
                summary_input.well_name,
            )
            final = base.model_copy(
                update={"status": CONCLUSION_STATUS_COMPLETED, "summary": summary},
            )
        else:
            final = base.model_copy(
                update={
                    "status": CONCLUSION_STATUS_FAILED,
                    "error": result.error or "empty LLM response",
                },
            )
        await self.conclusion_repo.upsert(final)
        await self.session.commit()
        logger.info(
            "Conclusion for incident id=%s level=%s: %s (confidence=%.2f, tokens=%s)",
            incident.id,
            incident.level,
            final.status,
            final.confidence,
            result.usage.total_tokens,
        )

    async def _build_summary_input(
        self,
        incident: DetectorIncident,
        cause: str,
    ) -> ConclusionSummaryInput:
        well = await WellRepository(self.session).get_by_id(id_=incident.well_id)
        rates = None
        if well is not None and well.abai_id is not None:
            rates = await WellRatesService(
                TelemetryRepository(self.session),
                TechRegimeRepository(self.session),
            ).get_for_well(well_id=well.id, abai_well_id=well.abai_id)
        return ConclusionSummaryInput(
            cause=cause,
            detector_code=incident.detector_code,
            level=incident.level,
            well_name=well.name if well else None,
            opened_at=incident.opened_at.isoformat(),
            last_seen_at=incident.last_seen_at.isoformat(),
            payload=incident.payload,
            oil_rate=rates.oil_rate if rates else None,
            liquid_rate=rates.liquid_rate if rates else None,
            water_cut=rates.water_cut if rates else None,
            plan_oil_rate=rates.plan_oil_rate if rates else None,
            plan_liquid_rate=rates.plan_liquid_rate if rates else None,
        )


async def main(incident_id: int, *, force: bool = False) -> None:
    async with session_makers["app"]() as session:
        await ConclusionGenerator(session).run(incident_id, force=force)


@celery_app.task(name="detectors.conclusion.generate")
def generate_conclusion(incident_id: int) -> None:
    run_async(main(incident_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident-id", type=int, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.incident_id, force=args.force))
