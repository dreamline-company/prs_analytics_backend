"""Состояние скважин для контура: кто стоит и почему, план Qн, авария, координаты.

Общая выборка для почасового подбора и для эндпоинтов, чтобы «стоит» и
«потеря» везде значили одно и то же. Время ABAI (статусы, ремонты) хранится
в UTC и отдаётся в местном.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.constants import (
    ABAI_STATUS_IDLE,
    PLAN_GRACE_DAYS,
    STOP_IDLE,
    STOP_REPAIR,
)
from apps.compensation.services.allocation import Point
from apps.detectors.models.incident import INCIDENT_LEVEL_ALARM
from apps.detectors.repositories import DetectorIncidentRepository, DetectorRepository
from apps.detectors.services import WellIncidentStatusService
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.org.services import wells_with_prefix
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.wells.models.well import Well
from apps.wells.repositories import (
    WellCoordRepository,
    WellOrgRepository,
    WellRepository,
    WellStatusRepository,
)
from apps.wells.services import CoordPointService, NGDUWellsService
from core.settings import get_settings
from shared.constants.ngdu import KMG_ONLY_OIL_FIELD_PREFIX, AbaiNGDUIDsEnum

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_local(moment: datetime) -> datetime:
    """Наивный UTC -> наивное местное время."""
    return (
        moment.replace(tzinfo=UTC).astimezone(settings.ZONE_INFO).replace(tzinfo=None)
    )


@dataclass(frozen=True, slots=True)
class Stop:
    kind: str  # STOP_IDLE / STOP_REPAIR
    text: str | None  # причина простоя или вид ремонта
    since: datetime  # местное время


@dataclass(slots=True)
class WellsState:
    stops: dict[int, Stop] = field(default_factory=dict)  # well_id -> почему стоит
    plans: dict[int, tuple[float, date]] = field(default_factory=dict)  # Qн, дата
    alarms: set[int] = field(default_factory=set)  # well_id с уровнем alarm


class CompensationStateService:
    def __init__(self, session: AsyncSession) -> None:
        self.org_repository = OrgRepository(session)
        self.ngdu_wells_service = NGDUWellsService(
            org_repository=self.org_repository,
            well_repository=WellRepository(session),
            well_org_repository=WellOrgRepository(session),
            oil_field_repository=OilFieldRepository(session),
        )
        self.status_repository = WellStatusRepository(session)
        self.repair_repository = RepairRepository(session)
        self.regime_repository = TechRegimeRepository(session)
        self.incident_status_service = WellIncidentStatusService(
            incident_repository=DetectorIncidentRepository(session),
            detector_repository=DetectorRepository(session),
        )
        self.coord_point_service = CoordPointService(
            well_coord_repository=WellCoordRepository(session),
        )

    async def scope_wells(
        self,
        ngdu_id: int,
        oil_field_id: int | None = None,
    ) -> list[Well]:
        """Скважины НГДУ (или месторождения); у Кайнармунайгаза — только VMB."""
        wells = await self.ngdu_wells_service.list_wells(
            ngdu_id,
            oil_field_id=oil_field_id,
        )
        ngdu = await self.org_repository.get_by_id(ngdu_id)
        if ngdu is not None and ngdu.abai_id == AbaiNGDUIDsEnum.KMG:
            wells = wells_with_prefix(wells, KMG_ONLY_OIL_FIELD_PREFIX)
        return wells

    async def is_excluded_kmg_well(self, well: Well) -> bool:
        """Скважина Кайнармунайгаза вне VMB — в контур не входит."""
        kmg = await self.org_repository.list_by_abai_ids([AbaiNGDUIDsEnum.KMG])
        if not kmg or well.name.startswith(f"{KMG_ONLY_OIL_FIELD_PREFIX}_"):
            return False
        kmg_wells = await self.ngdu_wells_service.list_wells(kmg[0].id)
        return any(item.id == well.id for item in kmg_wells)

    async def load(self, wells: list[Well], *, now: datetime) -> WellsState:
        """Состояние на ``now`` (наивный UTC)."""
        state = WellsState()
        if not wells:
            return state
        well_by_abai = {well.abai_id: well.id for well in wells}
        abai_ids = list(well_by_abai)

        intervals = await self.status_repository.list_intervals_by_abai_well_ids(
            abai_ids,
            since=now,
            until=now + timedelta(seconds=1),
        )
        for abai_well_id, dbeg, dend, code, name, reason in intervals:
            if code == ABAI_STATUS_IDLE and dbeg <= now < dend:
                state.stops[well_by_abai[abai_well_id]] = Stop(
                    STOP_IDLE,
                    reason or name,
                    to_local(dbeg),
                )
        # Ремонт важнее простоя: вид работ говорит больше, чем «ПРС» в статусе.
        # Время ремонтов ABAI, в отличие от статусов, уже местное.
        repairs = await self.repair_repository.list_current_by_abai_well_ids(
            abai_ids,
            now=to_local(now),
        )
        for abai_well_id, repair in repairs.items():
            state.stops[well_by_abai[abai_well_id]] = Stop(
                STOP_REPAIR,
                repair.repair_type_name_ru,
                repair.start_time,
            )

        regimes = await self.regime_repository.get_current_by_abai_well_ids(
            abai_ids,
            on_date=to_local(now).date(),
            grace_days=PLAN_GRACE_DAYS,
        )
        state.plans = {
            well_by_abai[abai_well_id]: (regime.oil, regime.start_date)
            for abai_well_id, regime in regimes.items()
            if regime.oil is not None and regime.oil > 0
        }

        statuses = await self.incident_status_service.get_for_wells(
            [well.id for well in wells],
        )
        state.alarms = {
            well_id
            for well_id, status in statuses.items()
            if status.level == INCIDENT_LEVEL_ALARM
        }
        return state

    async def points(self, wells: list[Well]) -> dict[int, Point | None]:
        """Координаты WGS 84 для расстояний; непригодная координата — None."""
        result: dict[int, Point | None] = {}
        for well in wells:
            point = await self.coord_point_service.resolve(well.coords_id)
            usable = point is not None and point.is_mappable
            result[well.id] = (point.lat, point.lon) if usable else None
        return result
