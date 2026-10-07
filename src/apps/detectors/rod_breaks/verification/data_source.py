"""Данные для проверки одного эпизода R2 из БД приложения.

Источники (все — наши копии):

- статус привода — ``telemetry_sdmo_fc_data.r_1999`` станции эпизода
  (``entity_id``), местное время;
- замеры ЦИТС — ``telemetry_well`` (нефть ``qm_oil``), местное время;
- техрежим по нефти — ``telemetry_tech_regime``;
- ремонты — ``repairs_repair`` (время ABAI местное, удалённые в ABAI не берутся);
- статусы — ``wells_well_status`` (местное время; перевод из UTC включается
  ``config.ABAI_STATUS_TIME_IS_UTC``).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.rod_breaks.verification import config
from apps.detectors.rod_breaks.verification.rule import (
    AbaiStatusEvent,
    Measurement,
    RepairEvent,
    StatusSample,
    VerificationInput,
)
from apps.repairs.models.repair import Repair
from apps.telemetry.models.sdmo import SdmoFcData
from apps.telemetry.models.tech_regime import TechRegime
from apps.telemetry.models.telemetry import Telemetry
from apps.wells.models.well import Well
from apps.wells.models.well_status import (
    WellStatus,
    WellStatusReason,
    WellStatusType,
)
from core.settings import get_settings

_STATUS_REGISTER = SdmoFcData.r_1999


@dataclass(frozen=True, slots=True)
class IncidentRef:
    """Снимок эпизода: ORM-объект после rollback/commit протухает."""

    id: int
    detector_code: str
    well_id: int
    entity_id: int | None
    opened_at: datetime


def _abai_to_local(moment: datetime) -> datetime:
    if not config.ABAI_STATUS_TIME_IS_UTC:
        return moment
    zone = get_settings().ZONE_INFO
    return moment.replace(tzinfo=UTC).astimezone(zone).replace(tzinfo=None)


def _local_to_abai(moment: datetime) -> datetime:
    if not config.ABAI_STATUS_TIME_IS_UTC:
        return moment
    zone = get_settings().ZONE_INFO
    return moment.replace(tzinfo=zone).astimezone(UTC).replace(tzinfo=None)


class R2VerificationSource:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, incident: IncidentRef) -> VerificationInput:
        t0 = incident.opened_at
        abai_well_id = await self.session.scalar(
            select(Well.abai_id).where(Well.id == incident.well_id),
        )
        return VerificationInput(
            t0=t0,
            status_samples=await self._status_samples(incident.entity_id, t0),
            measurements=await self._measurements(incident.well_id, t0),
            tech_regime_oil=await self._tech_regime_oil(abai_well_id, t0),
            repairs=await self._repairs(abai_well_id, t0),
            abai_statuses=await self._abai_statuses(abai_well_id, t0),
        )

    async def _status_samples(
        self,
        station_id: int | None,
        t0: datetime,
    ) -> list[StatusSample]:
        if station_id is None:
            return []
        # До конца окна + часы работы после самого позднего замера.
        until = t0 + timedelta(
            hours=config.WINDOW_HOURS + config.DRIVE_AFTER_MEASUREMENT_HOURS,
        )
        rows = await self.session.execute(
            select(SdmoFcData.savetime, _STATUS_REGISTER)
            .where(
                SdmoFcData.station_id == station_id,
                SdmoFcData.savetime >= t0,
                SdmoFcData.savetime < until,
                _STATUS_REGISTER.is_not(None),
            )
            .order_by(SdmoFcData.savetime),
        )
        return [StatusSample(at=at, code=int(code)) for at, code in rows.all()]

    async def _measurements(self, well_id: int, t0: datetime) -> list[Measurement]:
        rows = await self.session.execute(
            select(Telemetry.date_time, Telemetry.qm_oil, Telemetry.qv_liquid)
            .where(
                Telemetry.well_id == well_id,
                Telemetry.date_time
                > t0 - timedelta(days=config.OIL_NORM_LOOKBACK_DAYS),
                Telemetry.date_time <= t0 + timedelta(hours=config.WINDOW_HOURS),
            )
            .order_by(Telemetry.date_time),
        )
        return [
            Measurement(at=at, qm_oil=oil, qv_liquid=liquid)
            for at, oil, liquid in rows.all()
        ]

    async def _tech_regime_oil(
        self,
        abai_well_id: int | None,
        t0: datetime,
    ) -> float | None:
        if abai_well_id is None:
            return None
        return await self.session.scalar(
            select(TechRegime.oil)
            .where(
                TechRegime.abai_well_id == abai_well_id,
                TechRegime.start_date <= t0.date(),
            )
            .order_by(TechRegime.start_date.desc())
            .limit(1),
        )

    async def _repairs(
        self,
        abai_well_id: int | None,
        t0: datetime,
    ) -> list[RepairEvent]:
        if abai_well_id is None:
            return []
        rows = await self.session.execute(
            select(Repair.id, Repair.start_time, Repair.work_list).where(
                Repair.abai_well_id == abai_well_id,
                Repair.abai_deleted_at.is_(None),
                Repair.start_time >= t0 - timedelta(hours=config.REPAIR_LOOKBACK_HOURS),
                Repair.start_time <= t0 + timedelta(days=config.FINAL_DAYS),
            ),
        )
        return [
            RepairEvent(
                repair_id=repair_id,
                start=start,
                rod_break=config.ROD_BREAK_PATTERN in (work_list or "").lower(),
                work_list=work_list or "",
            )
            for repair_id, start, work_list in rows.all()
        ]

    async def _abai_statuses(
        self,
        abai_well_id: int | None,
        t0: datetime,
    ) -> list[AbaiStatusEvent]:
        """Интервалы, начавшиеся в окне, и интервал, покрывающий T0."""
        if abai_well_id is None:
            return []
        lo = _local_to_abai(t0 - timedelta(hours=config.ABAI_LOOKBACK_HOURS))
        hi = _local_to_abai(t0 + timedelta(days=config.FINAL_DAYS))
        t0_abai = _local_to_abai(t0)
        rows = await self.session.execute(
            select(
                WellStatus.dbeg,
                WellStatus.dend,
                WellStatusType.code,
                WellStatusReason.name_ru,
            )
            .join(WellStatusType, WellStatusType.abai_id == WellStatus.status)
            .outerjoin(WellStatusReason, WellStatusReason.abai_id == WellStatus.reason)
            .where(
                WellStatus.abai_well_id == abai_well_id,
                or_(
                    and_(WellStatus.dbeg >= lo, WellStatus.dbeg <= hi),
                    and_(WellStatus.dbeg <= t0_abai, WellStatus.dend > t0_abai),
                ),
            )
            .order_by(WellStatus.dbeg),
        )
        return [
            AbaiStatusEvent(
                since=_abai_to_local(dbeg),
                until=_abai_to_local(dend) if dend else None,
                code=code,
                reason=reason,
            )
            for dbeg, dend, code, reason in rows.all()
        ]
