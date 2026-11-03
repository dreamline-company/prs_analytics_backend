"""Состояние скважин для контура: кто стоит и почему, величина потери, авария.

Общая выборка для почасового подбора и для эндпоинтов, чтобы «стоит» и
«потеря» везде значили одно и то же. Потери (стоящие скважины и величина)
считает подбор раз в час (``services.losses``: таблица состояний + факт ABAI
за 10 суток до остановки) и пишет в ``compensation_loss``; эндпоинты читают
оттуда. Время везде местное наивное; наружу ``now`` приходит наивным UTC.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.repositories import CompensationLossRepository
from apps.compensation.services.allocation import Point
from apps.detectors.models.incident import INCIDENT_LEVEL_ALARM
from apps.detectors.repositories import DetectorIncidentRepository, DetectorRepository
from apps.detectors.services import WellIncidentStatusService
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.org.services import wells_with_prefix
from apps.wells.models.well import Well
from apps.wells.repositories import (
    WellCoordRepository,
    WellOrgRepository,
    WellRepository,
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
    # Величина потери, т/сут (факт ABAI или план), и дата расчёта; нет оценки —
    # скважины здесь нет (контур «no_plan»).
    losses: dict[int, tuple[float, date]] = field(default_factory=dict)
    loss_sources: dict[int, str] = field(default_factory=dict)  # fact / plan
    gzu: dict[int, int | None] = field(default_factory=dict)  # ГЗУ стоящей
    alarms: set[int] = field(default_factory=set)  # well_id с уровнем alarm


def apply_losses(state: WellsState, rows: Iterable, *, on_date: date) -> None:
    """Потери (строки ``compensation_loss`` или ``LossValue``) в состояние."""
    for row in rows:
        state.stops[row.well_id] = Stop(row.stop_kind, row.stop_text, row.stop_since)
        state.gzu[row.well_id] = row.gzu_abai_id
        if row.loss is not None and row.loss > 0:
            calculated = getattr(row, "calculated_at", None)
            state.losses[row.well_id] = (
                row.loss,
                calculated.date() if calculated else on_date,
            )
            source = getattr(row, "loss_source", None) or getattr(row, "source", None)
            if source:
                state.loss_sources[row.well_id] = source


class CompensationStateService:
    def __init__(self, session: AsyncSession) -> None:
        self.org_repository = OrgRepository(session)
        self.ngdu_wells_service = NGDUWellsService(
            org_repository=self.org_repository,
            well_repository=WellRepository(session),
            well_org_repository=WellOrgRepository(session),
            oil_field_repository=OilFieldRepository(session),
        )
        self.loss_repository = CompensationLossRepository(session)
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
        """Потери — из ``compensation_loss``, аварии — по детекторам."""
        state = WellsState()
        if not wells:
            return state
        rows = await self.loss_repository.list_by_well_ids([well.id for well in wells])
        apply_losses(state, rows, on_date=to_local(now).date())
        state.alarms = await self.alarms(wells)
        return state

    async def alarms(self, wells: list[Well]) -> set[int]:
        statuses = await self.incident_status_service.get_for_wells(
            [well.id for well in wells],
        )
        return {
            well_id
            for well_id, status in statuses.items()
            if status.level == INCIDENT_LEVEL_ALARM
        }

    async def points(self, wells: list[Well]) -> dict[int, Point | None]:
        """Координаты WGS 84 для расстояний; непригодная координата — None."""
        result: dict[int, Point | None] = {}
        for well in wells:
            point = await self.coord_point_service.resolve(well.coords_id)
            usable = point is not None and point.is_mappable
            result[well.id] = (point.lat, point.lon) if usable else None
        return result
