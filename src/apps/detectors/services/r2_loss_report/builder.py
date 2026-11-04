"""Сборка суточного отчёта «Обрыв штанг» (R2) на дату.

Правило не перезапускается: читаются записанные эпизоды R2, а ремонты, статусы
ABAI, станция, техрежим и замеры ГЗУ берутся на конец дня отчёта. Сначала
решается, кто попадает в отчёт (дешёвые запросы), подробности — только по ним.
Результат — xlsx в едином бакете и строка ``detectors_r2_loss_report``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.r2_loss_report import R2LossReportDTO
from apps.detectors.dto.internal.repositories.r2_loss_report import (
    UpsertR2LossReportDTO,
)
from apps.detectors.models.incident import DetectorIncident
from apps.detectors.models.r2_loss_report import (
    R2_LOSS_REPORT_STATUS_COMPLETED,
    R2_LOSS_REPORT_STATUS_FAILED,
)
from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorVerificationRepository,
)
from apps.detectors.repositories.r2_loss_report import DetectorR2LossReportRepository
from apps.detectors.services.daily_sheet.selection import day_bounds
from apps.detectors.services.r2_loss_report import config
from apps.detectors.services.r2_loss_report.errors import (
    R2LossReportDataNotReadyError,
    R2LossReportDateInFutureError,
)
from apps.detectors.services.r2_loss_report.render import file_name, render_xlsx
from apps.detectors.services.r2_loss_report.rows import (
    AbaiStatus,
    DriveFacts,
    Measure,
    RepairSpan,
    StatusInterval,
    WellFacts,
    build_row,
    chain_start,
    display_cdng,
    eligible_episodes,
    is_producing,
    reference_base,
    sort_rows,
    status_on,
    total_oil_loss,
)
from apps.detectors.services.r2_loss_report.station import StationReader
from apps.files.services.file import FileService
from apps.org.models.org import Org
from apps.org.repositories.org import OrgRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.models.telemetry import Telemetry
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository, SdmoStationRepository
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.models.well import Well
from apps.wells.repositories import (
    FundRevisionRepository,
    WellOrgRepository,
    WellStatusRepository,
)
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.constants.ngdu import NGDU_ORG_TYPE
from shared.database.s3.storage import AiobotoFileStorage
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()

# Целевой флот R2 — станции Danfoss VLT (как в rod_breaks.run_incidents).
_R2_STATION_TYPE = 6
_ERROR_MAX_CHARS = 1000
_MAX_ORG_HOPS = 10
# Конец дня отчёта как «последний миг суток» для интервалов статусов.
_INSTANT = timedelta(microseconds=1)
# Насколько раньше первого сигнала читать статусы ABAI — чтобы склеить простой.
_STATUS_HISTORY_DAYS = 30


def local_now() -> datetime:
    """Наивное местное время — в том же виде, что метки эпизодов и телеметрии."""
    return datetime.now(settings.ZONE_INFO).replace(tzinfo=None)


def file_key(report: R2LossReportDTO) -> str:
    built = report.built_at or local_now()
    return (
        f"detectors/r2_loss_report/{report.report_date:%Y-%m-%d}/"
        f"{built:%Y%m%dT%H%M%S}_{file_name(report.report_date)}"
    )


def cdng_name(abai_org_id: int | None, orgs: dict[int, Org]) -> str | None:
    """ЦДНГ — организация прямо под НГДУ в цепочке от привязки скважины.

    ``parent_id`` в нашем зеркале хранит ABAI id родителя.
    """
    org = orgs.get(abai_org_id) if abai_org_id is not None else None
    for _ in range(_MAX_ORG_HOPS):
        if org is None or org.parent_id is None:
            return None
        parent = orgs.get(org.parent_id)
        if parent is not None and parent.org_type_id == NGDU_ORG_TYPE:
            return org.name_ru_short or org.name_ru
        org = parent
    return None


@dataclass(frozen=True, slots=True)
class _Day:
    report_date: date
    day_start: datetime
    day_end: datetime


class R2LossReportBuilder:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: AiobotoFileStorage,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.now = now or local_now()
        self.incident_repo = DetectorIncidentRepository(session)
        self.verification_repo = DetectorVerificationRepository(session)
        self.report_repo = DetectorR2LossReportRepository(session)
        self.well_repo = WellRepository(session)
        self.well_org_repo = WellOrgRepository(session)
        self.fund_revision_repo = FundRevisionRepository(session)
        self.org_repo = OrgRepository(session)
        self.status_repo = WellStatusRepository(session)
        self.repair_repo = RepairRepository(session)
        self.regime_repo = TechRegimeRepository(session)
        self.telemetry_repo = TelemetryRepository(session)
        self.station_repo = SdmoStationRepository(session)
        self.fc_data_repo = SdmoFcDataRepository(session)
        self.stations = StationReader(session)

    async def build(self, report_date: date) -> R2LossReportDTO:
        """Собрать, сохранить файл и строку; вернуть содержимое без ссылки."""
        if report_date > self.now.date():
            raise R2LossReportDateInFutureError(
                details={"date": report_date.isoformat()},
            )
        day_start, day_end = day_bounds(report_date)
        day = _Day(report_date, day_start, day_end)
        await self._ensure_telemetry(report_date)

        incidents = await self.incident_repo.list_opened_between(
            detector_code=config.DETECTOR_CODE,
            opened_since=day_end - timedelta(days=config.LOOKBACK_DAYS),
            opened_before=day_end,
        )
        wells = {
            well.id: well
            for well in await self.well_repo.list_by_ids(
                list({incident.well_id for incident in incidents}),
            )
        }
        verifications = await self.verification_repo.get_map_by_incident_ids(
            [incident.id for incident in incidents],
        )
        episodes = eligible_episodes(
            incidents,
            verdicts={key: row.verdict for key, row in verifications.items()},
            repairs=await self._repairs(incidents, wells, day),
            day_end=day_end,
        )
        facts = await self._not_producing(episodes, wells, day)
        await self._enrich(facts, wells, day)
        rows = sort_rows([build_row(item, report_date=report_date) for item in facts])
        report = R2LossReportDTO(
            report_date=report_date,
            built_at=self.now,
            config_version=config.CONFIG_VERSION,
            wells_count=len(rows),
            total_oil_loss=total_oil_loss(rows),
            rows=rows,
            file_name=file_name(report_date),
        )
        await self._store(report)
        logger.info(
            "R2 loss report %s: candidates=%s wells=%s total=%s t/day file_id=%s",
            report_date,
            len(episodes),
            report.wells_count,
            report.total_oil_loss,
            report.file_id,
        )
        return report

    # --- отбор -------------------------------------------------------------------

    async def _ensure_telemetry(self, report_date: date) -> None:
        stations = await self.station_repo.get_list(
            QuerySpec(
                filters=(
                    SdmoStation.type_1900 == _R2_STATION_TYPE,
                    SdmoStation.well_id.is_not(None),
                ),
            ),
        )
        reporting = await self.fc_data_repo.list_reporting_station_ids(
            [station.id for station in stations],
            day=report_date,
        )
        if not reporting:
            raise R2LossReportDataNotReadyError(
                details={
                    "date": report_date.isoformat(),
                    "stations_total": len(stations),
                },
            )

    async def _repairs(
        self,
        incidents: Sequence[DetectorIncident],
        wells: dict[int, Well],
        day: _Day,
    ) -> dict[int, list[RepairSpan]]:
        if not incidents:
            return {}
        well_by_abai = {well.abai_id: well.id for well in wells.values()}
        earliest = min(incident.opened_at for incident in incidents)
        repairs = await self.repair_repo.list_covering_range(
            list(wells),
            list(well_by_abai),
            date_from=(earliest - timedelta(hours=config.REPAIR_LOOKBACK_HOURS)).date(),
            date_to=day.report_date,
        )
        spans: dict[int, list[RepairSpan]] = {}
        for repair in repairs:
            well_id = (
                repair.well_id
                if repair.well_id in wells
                else well_by_abai.get(repair.abai_well_id)
            )
            if well_id is not None:
                spans.setdefault(well_id, []).append(
                    RepairSpan(start=repair.start_time, end=repair.end_time),
                )
        return spans

    async def _not_producing(
        self,
        episodes: dict[int, list[DetectorIncident]],
        wells: dict[int, Well],
        day: _Day,
    ) -> list[WellFacts]:
        """Скважины, которые на конец дня не добывают, с началом их отказа."""
        if not episodes:
            return []
        statuses = await self._statuses(
            [wells[well_id].abai_id for well_id in episodes if well_id in wells],
            since=min(items[0].opened_at for items in episodes.values()),
            day=day,
        )
        result = []
        for well_id, items in episodes.items():
            well = wells.get(well_id)
            last = items[-1]
            reference = reference_base(await self._references(items))
            drive = await self._end_state(last, reference, day)
            facts = WellFacts(
                incident_id=last.id,
                well_id=well_id,
                well_name=well.name if well is not None else f"id:{well_id}",
                signal_at=last.opened_at,
                status=statuses.get(well.abai_id) if well is not None else None,
                drive=drive,
            )
            if is_producing(facts):
                continue
            start = await self._chain_start(items, drive.reference_moment)
            facts.incident_id = start.id
            facts.signal_at = start.opened_at
            facts.drive = replace(drive, base_moment=await self._signal_base(start))
            if start.entity_id is not None:
                facts.drive = await self.stations.details(
                    start.entity_id,
                    start.opened_at,
                    day_end=day.day_end,
                    drive=facts.drive,
                )
            result.append(facts)
        return result

    async def _references(
        self,
        episodes: Sequence[DetectorIncident],
    ) -> list[float | None]:
        """Норма момента за неделю до каждого эпизода; станция молчала — база
        эпизода."""
        window = timedelta(days=config.REFERENCE_DAYS)
        references = []
        for item in episodes:
            measured = None
            if item.entity_id is not None:
                measured = await self.stations.base_moment(
                    item.entity_id,
                    item.opened_at,
                    window=window,
                )
            references.append(measured or _payload_base(item))
        return references

    async def _signal_base(self, incident: DetectorIncident) -> float | None:
        """Момент за сутки до сигнала; станция молчала — база эпизода."""
        measured = None
        if incident.entity_id is not None:
            measured = await self.stations.base_moment(
                incident.entity_id,
                incident.opened_at,
                window=timedelta(hours=config.BASE_HOURS),
            )
        return measured or _payload_base(incident)

    async def _end_state(
        self,
        last: DetectorIncident,
        reference: float | None,
        day: _Day,
    ) -> DriveFacts:
        drive = DriveFacts(reference_moment=reference)
        if last.entity_id is None:
            return drive
        since = max(
            day.day_end - timedelta(hours=config.END_WINDOW_HOURS),
            last.opened_at + timedelta(minutes=config.DRIVE_AFTER_MINUTES),
        )
        total, running, moment = await self.stations.end_state(
            last.entity_id,
            since,
            day.day_end,
        )
        return replace(
            drive,
            has_end_data=total > 0,
            end_running=running,
            moment_end=moment,
        )

    async def _chain_start(
        self,
        episodes: Sequence[DetectorIncident],
        reference: float | None,
    ) -> DetectorIncident:
        station_id = episodes[-1].entity_id
        if station_id is None or reference is None:
            return episodes[-1]
        after = timedelta(minutes=config.DRIVE_AFTER_MINUTES)

        async def worked_between(since: datetime, until: datetime) -> bool:
            return await self.stations.worked(
                station_id,
                since + after,
                until,
                base=reference,
            )

        return await chain_start(episodes, worked_between=worked_between)

    async def _statuses(
        self,
        abai_ids: Sequence[int],
        *,
        since: datetime,
        day: _Day,
    ) -> dict[int, AbaiStatus]:
        intervals: dict[int, list[StatusInterval]] = {}
        for (
            abai_id,
            dbeg,
            dend,
            code,
            name,
            reason,
        ) in await self.status_repo.list_intervals_by_abai_well_ids(
            abai_ids,
            since=since - timedelta(days=_STATUS_HISTORY_DAYS),
            until=day.day_end,
        ):
            intervals.setdefault(abai_id, []).append((dbeg, dend, code, name, reason))
        moment = day.day_end - _INSTANT
        return {
            abai_id: status
            for abai_id, items in intervals.items()
            if (status := status_on(items, moment=moment)) is not None
        }

    # --- обогащение попавших ----------------------------------------------------

    async def _enrich(
        self,
        facts: Sequence[WellFacts],
        wells: dict[int, Well],
        day: _Day,
    ) -> None:
        if not facts:
            return
        well_ids = [item.well_id for item in facts]
        known = [wells[well_id] for well_id in well_ids if well_id in wells]
        regimes = await self.regime_repo.get_current_by_abai_well_ids(
            [well.abai_id for well in known],
            on_date=day.report_date,
            grace_days=config.PLAN_GRACE_DAYS,
        )
        measures = await self.telemetry_repo.list_by_well_ids_in_period(
            well_ids,
            date_time_from=min(item.signal_at for item in facts)
            - timedelta(days=config.GZU_LAST_BEFORE_DAYS),
            date_time_to=day.day_end,
        )
        in_gzu = await self.telemetry_repo.get_last_by_well_ids_before(
            well_ids,
            before=day.day_end,
        )
        cdngs = await self._cdngs(known)
        for item in facts:
            well = wells.get(item.well_id)
            regime = regimes.get(well.abai_id) if well is not None else None
            if regime is not None:
                item.plan_oil = regime.oil
                item.plan_liquid = regime.liquid
                item.plan_water_cut = regime.water_cut
            item.cdng = cdngs.get(item.well_id)
            item.in_gzu_data = item.well_id in in_gzu
            _split_measures(item, measures.get(item.well_id, []))

    async def _cdngs(self, wells: Sequence[Well]) -> dict[int, str]:
        """ЦДНГ скважины: из последней ревизии фонда (названия как у промысла),
        иначе по оргструктуре ABAI."""
        revisions = await self.fund_revision_repo.get_latest_by_well_ids(
            [well.id for well in wells],
        )
        orgs: dict[int, Org] | None = None
        names: dict[int, str] = {}
        for well in wells:
            revision = revisions.get(well.id)
            name = revision.cdng_name if revision is not None else None
            if not name:
                if orgs is None:
                    orgs = {org.abai_id: org for org in await self.org_repo.list_all()}
                link = await self.well_org_repo.get_current_by_abai_well_id(
                    well.abai_id,
                )
                name = cdng_name(link.abai_org_id if link else None, orgs)
            if (shown := display_cdng(name)) is not None:
                names[well.id] = shown
        return names

    # --- сохранение ------------------------------------------------------------

    async def _store(self, report: R2LossReportDTO) -> None:
        """Файл в S3 (FileService сам коммитит строку файла), затем строка отчёта.

        Неудача рендера или загрузки фиксируется строкой ``failed`` — если за
        дату ещё нет готового отчёта: неудачная пересборка его не затирает.
        """
        base = {
            "report_date": report.report_date,
            "wells_count": report.wells_count,
            "total_oil_loss": report.total_oil_loss,
            "built_at": report.built_at,
            "config_version": report.config_version,
        }
        try:
            db_file = await FileService(self.session, self.storage).upload(
                render_xlsx(report),
                file_key(report),
            )
            report.file_id = db_file.id
            await self.report_repo.upsert(
                UpsertR2LossReportDTO(
                    **base,
                    status=R2_LOSS_REPORT_STATUS_COMPLETED,
                    file_id=db_file.id,
                    content=[row.model_dump(mode="json") for row in report.rows],
                ),
            )
            await self.session.commit()
        except Exception as exc:
            logger.exception("R2 loss report %s failed to store", report.report_date)
            await self.session.rollback()
            stored = await self.report_repo.get_by_date(report.report_date)
            if stored is None or stored.status != R2_LOSS_REPORT_STATUS_COMPLETED:
                await self.report_repo.upsert(
                    UpsertR2LossReportDTO(
                        **base,
                        status=R2_LOSS_REPORT_STATUS_FAILED,
                        error=repr(exc)[:_ERROR_MAX_CHARS],
                    ),
                )
                await self.session.commit()
            raise


def _payload_base(incident: DetectorIncident) -> float | None:
    """База из эпизода — запасная норма, если станция до сигнала молчала."""
    value = (incident.payload or {}).get("base_moment")
    return float(value) if isinstance(value, int | float) else None


def _split_measures(facts: WellFacts, rows: Sequence[Telemetry]) -> None:
    """Замеры скважины → до сигнала (окно 30 сут), последний до, после."""
    window_start = facts.signal_at - timedelta(days=config.GZU_BEFORE_DAYS)
    for row in rows:
        measure = Measure(at=row.date_time, liquid=row.qv_liquid, oil=row.qm_oil)
        if row.date_time >= facts.signal_at:
            facts.measures_after.append(measure)
            continue
        if measure.liquid is not None:
            facts.last_before = measure
        if row.date_time >= window_start:
            facts.measures_before.append(measure)
