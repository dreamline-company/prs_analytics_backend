"""Сводка по скважине для обычного режима — без аварий виджет заключения пуст.

Только чтение существующих таблиц, при каждом запросе: состояние, привод
СДМО за 14 суток, замеры ЦИТС за 60, техрежим, ГДИС, ремонты, эпизоды
детекторов за 90 суток, донорство в контуре компенсации, последний ИИ-разбор
динамограммы. Цифры и наблюдения — ``services.well_overview.rules``.
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationRecommendationRepository,
)
from apps.detectors.models.incident import INCIDENT_STATUS_ACTIVE, DetectorIncident
from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
    DetectorVerificationRepository,
)
from apps.repairs.repositories.ai_results import RepairDynamogramAIResultRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import (
    PUMP_MOMENT_REGISTER,
    ROTOR_SPEED_REGISTER,
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.drive import DRIVE_RUNNING_VLT_CODES
from apps.telemetry.services.sdmo_scale import SdmoRegisterScaler
from apps.wells.dto.internal.dynamogram import WellDynamogramAIDTO
from apps.wells.dto.internal.well_overview import WellOverviewDTO
from apps.wells.models.well import Well
from apps.wells.repositories import (
    GdisCurrentValueRepository,
    GdisMetricRepository,
    WellDriveStateRepository,
    WellRepository,
    WellStateRepository,
    WellStateRunRepository,
)
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.services import WellGdisService
from apps.wells.services.well_overview.rules import (
    DRIVE_WINDOW_DAYS,
    INCIDENTS_WINDOW_DAYS,
    TREND_WINDOW,
    DonorFact,
    DriveDay,
    DynamogramFact,
    IncidentFact,
    Measurement,
    OverviewInputs,
    RepairFact,
    build_overview,
)
from apps.wells.services.well_state.read import WellStateReadService
from apps.wells.use_cases.get_well_card import WellNotFoundError
from core.settings import get_settings
from shared.repository.sqlalchemy import QuerySpec

# Типы станций (type_1900): у ШГН момент 1991 едет uint16 (отрицательный —
# как 65536 − x), у ЭВН встречаются Int32-выбросы — как в сводке привода
# ИИ-чата.
STATION_TYPE_SHGN = 1
STATION_TYPE_EVN = 6
EVN_MOMENT_CLIP = (0.0, 10000.0)
# Сколько последних динамограмм смотреть в поисках готового ИИ-разбора.
DYNAMOGRAM_LOOKBACK = 5
COMPLETED = "completed"


def local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


class GetWellOverviewUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.well_repository = WellRepository(session)

    async def execute(
        self,
        well_id: int,
        *,
        now: datetime | None = None,
    ) -> WellOverviewDTO:
        now = now or local_now()
        well = await self.well_repository.get_by_id(id_=well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": well_id})

        state = await WellStateReadService(
            state_repository=WellStateRepository(self.session),
            run_repository=WellStateRunRepository(self.session),
        ).get_for_well(well.id)
        station = await self._primary_station(well)
        drive_running = drive_since = None
        speed_units = None
        drive_days: list[DriveDay] = []
        if station is not None:
            drive = (
                await WellDriveStateRepository(self.session).get_map_by_station_ids(
                    [station.id],
                )
            ).get(station.id)
            if drive is not None:
                drive_running, drive_since = drive.running, drive.since
            drive_days, speed_units = await self._drive_days(station, now)

        regime = await TechRegimeRepository(self.session).get_last_by_abai_well_id(
            well.abai_id,
        )
        dynamic_level = await WellGdisService(
            gdis_metric_repository=GdisMetricRepository(self.session),
            gdis_current_value_repository=GdisCurrentValueRepository(self.session),
        ).get_last_dynamic_level(well.abai_id)

        items, observations = build_overview(
            OverviewInputs(
                now=now,
                state=state,
                drive_running=drive_running,
                drive_since=drive_since,
                speed_units=speed_units,
                drive_days=drive_days,
                plan_oil=regime.oil if regime else None,
                plan_liquid=regime.liquid if regime else None,
                measurements=await self._measurements(well, now),
                dynamic_level=(
                    (dynamic_level.h_din_m, dynamic_level.meas_date)
                    if dynamic_level
                    else None
                ),
                repairs=await self._repairs(well),
                incidents=await self._incidents(well, now),
                donor=await self._donor(well),
                dynamogram=await self._dynamogram(well),
            ),
        )
        return WellOverviewDTO(
            well_id=well.id,
            well_name=well.name,
            as_of=now,
            state=state,
            items=items,
            observations=observations,
        )

    async def _primary_station(self, well: Well) -> SdmoStation | None:
        """Станция с самым свежим отсчётом среди активных станций скважины."""
        stations = [
            station
            for station in await SdmoStationRepository(self.session).list_by_well_id(
                well.id,
            )
            if station.active is not False
        ]
        if not stations:
            return None
        drives = await WellDriveStateRepository(self.session).get_map_by_station_ids(
            [station.id for station in stations],
        )
        return max(
            stations,
            key=lambda station: (
                drives.get(station.id) is not None
                and drives[station.id].last_seen_at is not None,
                drives[station.id].last_seen_at
                if drives.get(station.id) is not None
                and drives[station.id].last_seen_at is not None
                else datetime.min,  # noqa: DTZ901 — только для сравнения
            ),
        )

    async def _drive_days(
        self,
        station: SdmoStation,
        now: datetime,
    ) -> tuple[list[DriveDay], str | None]:
        since = (now - timedelta(days=DRIVE_WINDOW_DAYS)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        rows = await SdmoFcDataRepository(self.session).list_drive_stats(
            station.id,
            since=since,
            until=now,
            bucket_hours=24,
            drive_work_codes=sorted(DRIVE_RUNNING_VLT_CODES),
            moment_clip=(
                EVN_MOMENT_CLIP if station.type_1900 == STATION_TYPE_EVN else None
            ),
            moment_wrap=station.type_1900 == STATION_TYPE_SHGN,
        )
        scaler = await SdmoRegisterScaler.load(SdmoFcRegRepository(self.session))
        days = [
            DriveDay(
                day=row["bucket"].date(),
                speed=scaler.scale(
                    row["speed_median"],
                    addr=ROTOR_SPEED_REGISTER,
                    type_1900=station.type_1900,
                ),
                moment=scaler.scale(
                    row["moment_median"],
                    addr=PUMP_MOMENT_REGISTER,
                    type_1900=station.type_1900,
                ),
            )
            for row in rows
        ]
        units = scaler.units(addr=ROTOR_SPEED_REGISTER, type_1900=station.type_1900)
        return days, units

    async def _measurements(self, well: Well, now: datetime) -> list[Measurement]:
        rows = await TelemetryRepository(self.session).list_by_well_id_in_period(
            well.id,
            date_time_from=now - 2 * TREND_WINDOW,
        )
        return [
            Measurement(time=row.date_time, oil=row.qm_oil, liquid=row.qv_liquid)
            for row in rows
        ]

    async def _repairs(self, well: Well) -> list[RepairFact]:
        rows = await RepairRepository(self.session).list_with_type_by_well_abai_ids(
            [well.abai_id],
        )
        return [
            RepairFact(start=repair.start_time, end=repair.end_time, type_name=name)
            for repair, name in rows
        ]

    async def _incidents(self, well: Well, now: datetime) -> list[IncidentFact]:
        incidents = await DetectorIncidentRepository(self.session).get_list(
            QuerySpec(
                filters=(
                    DetectorIncident.well_id == well.id,
                    DetectorIncident.opened_at
                    >= now - timedelta(days=INCIDENTS_WINDOW_DAYS),
                ),
            ),
        )
        if not incidents:
            return []
        names = {
            detector.code: detector.name_ru
            for detector in await DetectorRepository(self.session).get_list(
                QuerySpec(),
            )
        }
        verdicts = await DetectorVerificationRepository(
            self.session,
        ).get_map_by_incident_ids([incident.id for incident in incidents])
        return [
            IncidentFact(
                title=names.get(incident.detector_code) or incident.detector_code,
                level=incident.level,
                opened_at=incident.opened_at,
                active=incident.status == INCIDENT_STATUS_ACTIVE,
                verdict=(
                    verdicts[incident.id].verdict if incident.id in verdicts else None
                ),
            )
            for incident in incidents
        ]

    async def _donor(self, well: Well) -> DonorFact | None:
        donors = await CompensationDonorRepository(self.session).list_by_well_ids(
            [well.id],
        )
        if not donors:
            return None
        donor = donors[0]
        pairs = await CompensationRecommendationRepository(
            self.session,
        ).list_open_by_donor_ids([donor.id])
        loss_name = pair_status = None
        if pairs:
            loss = await self.well_repository.get_by_id(id_=pairs[0].loss_well_id)
            loss_name = loss.name if loss is not None else None
            pair_status = pairs[0].status
        return DonorFact(
            speed=donor.speed,
            step_percent=donor.step_percent,
            gain=donor.gain,
            risk=donor.risk,
            loss_well_name=loss_name,
            pair_status=pair_status,
        )

    async def _dynamogram(self, well: Well) -> DynamogramFact | None:
        """Последний готовый ИИ-разбор среди последних динамограмм (ШГН)."""
        dynamograms = sorted(
            await DynamogramRepository(self.session).list_by_well_id(well.id),
            key=lambda item: item.snapshot_time,
            reverse=True,
        )[:DYNAMOGRAM_LOOKBACK]
        if not dynamograms:
            return None
        results = {
            row.dynamogram_id: row
            for row in await RepairDynamogramAIResultRepository(
                self.session,
            ).list_by_dynamogram_ids([item.id for item in dynamograms])
        }
        for dynamogram in dynamograms:
            row = results.get(dynamogram.id)
            if row is None or row.status != COMPLETED:
                continue
            ai = WellDynamogramAIDTO.from_result(
                status=row.status,
                prompt_version=row.prompt_version,
                model_name=row.model_name,
                processed_at=row.processed_at,
                error=row.error,
                result=row.result,
            )
            return DynamogramFact(
                time=dynamogram.snapshot_time,
                condition=ai.condition,
                severity=ai.severity,
                fill_pct=ai.fill_pct,
            )
        return None
