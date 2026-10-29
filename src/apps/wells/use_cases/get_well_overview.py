"""Сводка по скважине для обычного режима — без аварий виджет заключения пуст.

Только чтение существующих таблиц, при каждом запросе: состояние, привод
СДМО (последний отсчёт и суточные медианы за 14 полных суток), замеры ЦИТС за
60 суток, действующий техрежим, ГДИС, ремонты, эпизоды детекторов (за 90 суток
и все активные), донорство в контуре компенсации, последний ИИ-разбор
динамограммы. Цифры и наблюдения — ``services.well_overview.rules``.
"""

from datetime import datetime, timedelta

from sqlalchemy import or_
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
from apps.repairs.models.analytics import AI_STATUS_COMPLETED
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
from apps.wells.models.well_state import WellDriveState
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
    DRIVE_MIN_ROTATING_SHARE,
    DRIVE_WINDOW_DAYS,
    INCIDENTS_WINDOW_DAYS,
    MEASUREMENTS_WINDOW,
    DonorFact,
    DriveDay,
    DynamogramFact,
    IncidentFact,
    Measurement,
    OverviewInputs,
    RepairFact,
    build_overview,
)
from apps.wells.services.well_state.drive import DriveSnapshot
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
# План — действующий техрежим; режимы месячные и приезжают с лагом.
PLAN_GRACE_DAYS = 31


def local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


def _snapshot(row: WellDriveState) -> DriveSnapshot:
    return DriveSnapshot(
        running=row.running,
        since=row.since,
        reconfirming=row.reconfirming,
        last_sample_at=row.last_sample_at,
        last_seen_at=row.last_seen_at,
    )


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

        states = await WellStateReadService(
            state_repository=WellStateRepository(self.session),
            run_repository=WellStateRunRepository(self.session),
        ).get_for_wells([well.id], now=now)
        state = states[well.id]

        drive_running = drive_since = speed = speed_units = None
        drive_days: list[DriveDay] = []
        station, snapshot = await self._primary_station(well, now)
        if station is not None:
            scaler = await SdmoRegisterScaler.load(SdmoFcRegRepository(self.session))
            # Привод — по тем же правилам, что итоговое состояние: только
            # свежее подтверждённое состояние.
            if snapshot is not None and snapshot.is_known(now):
                drive_running, drive_since = snapshot.running, snapshot.since
            speed = await self._speed(station, scaler)
            speed_units = scaler.units(
                addr=ROTOR_SPEED_REGISTER,
                type_1900=station.type_1900,
            )
            drive_days = await self._drive_days(station, scaler, now)

        regimes = await TechRegimeRepository(
            self.session,
        ).get_current_by_abai_well_ids(
            [well.abai_id],
            on_date=now.date(),
            grace_days=PLAN_GRACE_DAYS,
        )
        regime = regimes.get(well.abai_id)
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
                speed=speed,
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

    async def _primary_station(
        self,
        well: Well,
        now: datetime,
    ) -> tuple[SdmoStation | None, DriveSnapshot | None]:
        """Станция и её автомат — тем же ключом, что итоговое состояние:
        известное состояние привода важнее, затем самый свежий пригодный
        отсчёт. Автомат другой скважины (станцию перепривязали) не берётся."""
        stations = [
            station
            for station in await SdmoStationRepository(self.session).list_by_well_id(
                well.id,
            )
            if station.active is not False
        ]
        if not stations:
            return None, None
        rows = await WellDriveStateRepository(self.session).get_map_by_station_ids(
            [station.id for station in stations],
        )
        candidates = []
        for station in stations:
            row = rows.get(station.id)
            snapshot = (
                _snapshot(row)
                if row is not None and row.well_id == station.well_id
                else None
            )
            last_sample = snapshot.last_sample_at if snapshot else None
            candidates.append(
                (
                    snapshot is not None and snapshot.is_known(now),
                    last_sample is not None,
                    last_sample or now,
                    station.id,
                    station,
                    snapshot,
                ),
            )
        best = max(candidates, key=lambda item: item[:4])
        return best[4], best[5]

    async def _speed(
        self,
        station: SdmoStation,
        scaler: SdmoRegisterScaler,
    ) -> float | None:
        """Скорость по последнему отсчёту, как в карточке. Без коэффициента
        для типа станции значение сырое — не отдаётся, чтобы не сравнивать
        его с пулом доноров."""
        if scaler.koef(addr=ROTOR_SPEED_REGISTER, type_1900=station.type_1900) is None:
            return None
        last = await SdmoFcDataRepository(
            self.session,
        ).get_last_pump_parameters_by_stations([station.id])
        if last is None:
            return None
        return scaler.scale(
            last["pump_speed"],
            addr=ROTOR_SPEED_REGISTER,
            type_1900=station.type_1900,
        )

    async def _drive_days(
        self,
        station: SdmoStation,
        scaler: SdmoRegisterScaler,
        now: datetime,
    ) -> list[DriveDay]:
        """Медианы момента за полные сутки; сутки, когда привод больше стоял,
        чем вращался, отбрасываются — медиана по стоянке не нагрузка."""
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await SdmoFcDataRepository(self.session).list_drive_stats(
            station.id,
            since=today - timedelta(days=DRIVE_WINDOW_DAYS),
            until=today,
            bucket_hours=24,
            drive_work_codes=sorted(DRIVE_RUNNING_VLT_CODES),
            moment_clip=(
                EVN_MOMENT_CLIP if station.type_1900 == STATION_TYPE_EVN else None
            ),
            moment_wrap=station.type_1900 == STATION_TYPE_SHGN,
        )
        return [
            DriveDay(
                day=row["bucket"].date(),
                moment=scaler.scale(
                    row["moment_median"],
                    addr=PUMP_MOMENT_REGISTER,
                    type_1900=station.type_1900,
                ),
            )
            for row in rows
            if row["speed_samples"]
            and row["rotating"] / row["speed_samples"] >= DRIVE_MIN_ROTATING_SHARE
        ]

    async def _measurements(self, well: Well, now: datetime) -> list[Measurement]:
        rows = await TelemetryRepository(self.session).list_by_well_id_in_period(
            well.id,
            date_time_from=now - MEASUREMENTS_WINDOW,
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
        """Эпизоды за 90 суток и все активные — как статус детекторов, который
        берёт активные без окна."""
        incidents = await DetectorIncidentRepository(self.session).get_list(
            QuerySpec(
                filters=(
                    DetectorIncident.well_id == well.id,
                    or_(
                        DetectorIncident.opened_at
                        >= now - timedelta(days=INCIDENTS_WINDOW_DAYS),
                        DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
                    ),
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
        pair = pairs[0] if pairs else None
        loss_name = None
        if pair is not None:
            loss = await self.well_repository.get_by_id(id_=pair.loss_well_id)
            loss_name = loss.name if loss is not None else None
        return DonorFact(
            speed=donor.speed,
            step_percent=donor.step_percent,
            gain=donor.gain,
            risk=donor.risk,
            loss_well_name=loss_name,
            pair_status=pair.status if pair is not None else None,
            speed_to=pair.speed_to if pair is not None else None,
        )

    async def _dynamogram(self, well: Well) -> DynamogramFact | None:
        """Последний готовый ИИ-разбор среди последних динамограмм (ШГН)."""
        dynamograms = await DynamogramRepository(self.session).list_latest_by_well_id(
            well.id,
            limit=DYNAMOGRAM_LOOKBACK,
        )
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
            if row is None or row.status != AI_STATUS_COMPLETED:
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
