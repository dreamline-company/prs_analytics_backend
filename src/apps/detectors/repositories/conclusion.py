from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.detectors.dto.internal.repositories.conclusion import (
    CreateConclusionDTO,
    CreateConclusionFeedbackDTO,
    UpdateConclusionDTO,
    UpdateConclusionFeedbackDTO,
)
from apps.detectors.models.conclusion import (
    CONCLUSION_STATUS_COMPLETED,
    DetectorConclusion,
    DetectorConclusionFeedback,
)
from apps.detectors.models.incident import (
    INCIDENT_STATUS_ACTIVE,
    DetectorIncident,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class DetectorConclusionRepository(
    AsyncAlchemyRepository[
        CreateConclusionDTO,
        UpdateConclusionDTO,
        DetectorConclusion,
    ],
):
    model = DetectorConclusion

    async def get_by_incident_and_level(
        self,
        *,
        incident_id: int,
        level: str,
    ) -> DetectorConclusion | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    DetectorConclusion.incident_id == incident_id,
                    DetectorConclusion.level == level,
                ),
            ),
        )

    async def upsert(self, data: CreateConclusionDTO) -> DetectorConclusion:
        """Записать заключение; конфликт по (incident_id, level) — перезапись.

        Конфликт возможен только у pending/failed строки (completed повторно не
        генерится — задача выходит раньше), поэтому перезапись безопасна:
        completed-результат затирает свою же неудачную попытку.
        """
        stmt = (
            pg_insert(DetectorConclusion)
            .values(**data.model_dump())
            .on_conflict_do_update(
                constraint="uq_detectors_conclusion_incident_level",
                set_={
                    "status": data.status,
                    "cause": data.cause,
                    "recommendations": data.recommendations,
                    "confidence": data.confidence,
                    "summary": data.summary,
                    "error": data.error,
                    "model_name": data.model_name,
                    "prompt_version": data.prompt_version,
                    "updated_at": func.now(),
                },
            )
            .returning(DetectorConclusion)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_by_incident_ids(
        self,
        incident_ids: Sequence[int],
        *,
        status: str | None = CONCLUSION_STATUS_COMPLETED,
    ) -> Sequence[DetectorConclusion]:
        if not incident_ids:
            return ()
        filters = [DetectorConclusion.incident_id.in_(incident_ids)]
        if status is not None:
            filters.append(DetectorConclusion.status == status)
        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(DetectorConclusion.id.desc(),),
            ),
        )

    async def list_by_well_id(
        self,
        *,
        well_id: int,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[DetectorConclusion]:
        """История заключений скважины: свежие сверху, любые статусы."""
        return await self.get_list(
            QuerySpec(
                filters=(DetectorConclusion.well_id == well_id,),
                order_by=(DetectorConclusion.id.desc(),),
                limit=limit,
                offset=offset,
            ),
        )

    async def list_active_incidents_missing_conclusion(
        self,
        detector_codes: Sequence[str],
        *,
        limit: int = 100,
    ) -> Sequence[DetectorIncident]:
        """Активные эпизоды без completed-заключения под их текущий уровень.

        Страховка подметальщика: ловит потерянные задачи генерации, упавшие
        LLM-попытки (failed) и эпизоды, открытые до внедрения заключений.
        """
        stmt = (
            select(DetectorIncident)
            .outerjoin(
                DetectorConclusion,
                (DetectorConclusion.incident_id == DetectorIncident.id)
                & (DetectorConclusion.level == DetectorIncident.level)
                & (DetectorConclusion.status == CONCLUSION_STATUS_COMPLETED),
            )
            .where(
                DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
                DetectorIncident.detector_code.in_(detector_codes),
                DetectorConclusion.id.is_(None),
            )
            .order_by(DetectorIncident.id)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class DetectorConclusionFeedbackRepository(
    AsyncAlchemyRepository[
        CreateConclusionFeedbackDTO,
        UpdateConclusionFeedbackDTO,
        DetectorConclusionFeedback,
    ],
):
    model = DetectorConclusionFeedback
