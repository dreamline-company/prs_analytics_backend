from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.detectors.dto.internal.repositories.incident import (
    CreateDetectorCursorDTO,
    CreateDetectorDTO,
    OpenIncidentDTO,
    UpdateDetectorCursorDTO,
    UpdateDetectorDTO,
    UpdateIncidentDTO,
)
from apps.detectors.models.incident import (
    CLOSE_REASON_RECOVERED,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_STATUS_ACTIVE,
    INCIDENT_STATUS_NORMALIZED,
    Detector,
    DetectorCursor,
    DetectorIncident,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class DetectorRepository(
    AsyncAlchemyRepository[CreateDetectorDTO, UpdateDetectorDTO, Detector],
):
    model = Detector

    async def get_by_code(self, code: str) -> Detector | None:
        return await self.get_one(QuerySpec(filters=(Detector.code == code,)))

    async def list_enabled_by_source(self, source: str) -> Sequence[Detector]:
        return await self.get_list(
            QuerySpec(
                filters=(Detector.source == source, Detector.enabled.is_(True)),
                order_by=(Detector.code,),
            ),
        )


class DetectorIncidentRepository(
    AsyncAlchemyRepository[OpenIncidentDTO, UpdateIncidentDTO, DetectorIncident],
):
    model = DetectorIncident

    async def get_active(
        self,
        *,
        detector_code: str,
        well_id: int,
        reason_code: str,
    ) -> DetectorIncident | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    DetectorIncident.detector_code == detector_code,
                    DetectorIncident.well_id == well_id,
                    DetectorIncident.reason_code == reason_code,
                    DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
                ),
            ),
        )

    async def get_by_opened(
        self,
        *,
        detector_code: str,
        well_id: int,
        reason_code: str,
        opened_at: datetime,
    ) -> DetectorIncident | None:
        """Эпизод с этим детерминированным opened_at (любой статус).

        opened_at считается из границ корзин, поэтому повторная обработка той
        же истории даёт то же значение — по нему распознаётся уже записанный
        (в т.ч. закрытый) эпизод при перечитывании истории.
        """
        return await self.get_one(
            QuerySpec(
                filters=(
                    DetectorIncident.detector_code == detector_code,
                    DetectorIncident.well_id == well_id,
                    DetectorIncident.reason_code == reason_code,
                    DetectorIncident.opened_at == opened_at,
                ),
            ),
        )

    async def upsert_active(self, data: OpenIncidentDTO) -> None:
        """Открыть эпизод или обновить существующий активный — один оператор.

        Конфликт по частичному уникальному индексу (активный эпизод уже есть)
        превращает INSERT в UPDATE:
          - last_seen_at двигается вперёд (не назад — на случай гонки);
          - level только повышается (alarm не деградирует в warning);
          - escalated_at ставится один раз (первая эскалация);
          - opened_at/detected_at исходного эпизода сохраняются.
        """
        stmt = pg_insert(DetectorIncident).values(
            detector_code=data.detector_code,
            well_id=data.well_id,
            entity_id=data.entity_id,
            reason_code=data.reason_code,
            level=data.level,
            status=INCIDENT_STATUS_ACTIVE,
            opened_at=data.opened_at,
            detected_at=data.detected_at,
            last_seen_at=data.last_seen_at,
            escalated_at=data.escalated_at,
            config_version=data.config_version,
            payload=data.payload,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["detector_code", "well_id", "reason_code"],
            index_where=text("status = 'active'"),
            set_={
                "last_seen_at": func.greatest(
                    DetectorIncident.last_seen_at,
                    stmt.excluded.last_seen_at,
                ),
                "level": func.coalesce(
                    func.nullif(DetectorIncident.level, "warning"),
                    stmt.excluded.level,
                ),
                "escalated_at": func.coalesce(
                    DetectorIncident.escalated_at,
                    stmt.excluded.escalated_at,
                ),
                "payload": stmt.excluded.payload,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)

    async def normalize(
        self,
        *,
        detector_code: str,
        well_id: int,
        reason_code: str,
        normalized_at: datetime,
        close_reason: str = CLOSE_REASON_RECOVERED,
    ) -> None:
        await self.session.execute(
            update(DetectorIncident)
            .where(
                DetectorIncident.detector_code == detector_code,
                DetectorIncident.well_id == well_id,
                DetectorIncident.reason_code == reason_code,
                DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
            )
            .values(
                status=INCIDENT_STATUS_NORMALIZED,
                normalized_at=normalized_at,
                close_reason=close_reason,
            ),
        )

    async def list_active_by_well_id(
        self,
        well_id: int,
    ) -> Sequence[DetectorIncident]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    DetectorIncident.well_id == well_id,
                    DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
                ),
                order_by=(DetectorIncident.opened_at,),
            ),
        )

    async def count_active_alarms(self) -> int:
        result = await self.session.execute(
            select(func.count()).where(
                DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
                DetectorIncident.level == INCIDENT_LEVEL_ALARM,
            ),
        )
        return int(result.scalar_one())


class DetectorCursorRepository(
    AsyncAlchemyRepository[
        CreateDetectorCursorDTO,
        UpdateDetectorCursorDTO,
        DetectorCursor,
    ],
):
    model = DetectorCursor

    async def get_map(
        self,
        detector_code: str,
        entity_ids: Sequence[int] | None = None,
    ) -> dict[int, DetectorCursor]:
        filters = [DetectorCursor.detector_code == detector_code]
        if entity_ids is not None:
            filters.append(DetectorCursor.entity_id.in_(entity_ids))
        rows = await self.get_list(QuerySpec(filters=tuple(filters)))
        return {row.entity_id: row for row in rows}

    async def upsert(
        self,
        *,
        detector_code: str,
        entity_id: int,
        last_event_at: datetime,
        last_run_at: datetime,
    ) -> None:
        stmt = pg_insert(DetectorCursor).values(
            detector_code=detector_code,
            entity_id=entity_id,
            last_event_at=last_event_at,
            last_run_at=last_run_at,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_detectors_cursor_detector_code_entity_id",
            set_={
                "last_event_at": func.greatest(
                    DetectorCursor.last_event_at,
                    stmt.excluded.last_event_at,
                ),
                "last_run_at": stmt.excluded.last_run_at,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
