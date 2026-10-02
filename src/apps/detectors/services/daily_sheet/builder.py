"""Сборка суточной ведомости R2 / R9 / R10 по НГДУ на дату.

Правило не перезапускается: читаются записанные эпизоды, состояние «на дату»
восстанавливается по меткам времени, обогащение (дебиты, техрежим, ремонты,
остановки, уровень) тоже берётся на дату. Результат — docx в едином бакете и
строка ``detectors_daily_sheet`` с содержимым. LLM в сборке не участвует:
готовое ИИ-заключение эпизода, если есть, только цитируется.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.cits_events.config import WINDOW_D as CITS_WINDOW_D
from apps.detectors.cits_events.incident_config import FINDING_STALE
from apps.detectors.conclusion import catalog
from apps.detectors.dto.internal.daily_sheet import (
    DailySheetCoverageDTO,
    DailySheetDTO,
    DailySheetOilFieldDTO,
    DailySheetRowDTO,
    DailySheetTopItemDTO,
)
from apps.detectors.dto.internal.repositories.daily_sheet import UpsertDailySheetDTO
from apps.detectors.models.conclusion import (
    CONCLUSION_STATUS_COMPLETED,
    DetectorConclusion,
)
from apps.detectors.models.daily_sheet import (
    DAILY_SHEET_STATUS_COMPLETED,
    DAILY_SHEET_STATUS_FAILED,
)
from apps.detectors.models.incident import DetectorIncident
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorCursorRepository,
    DetectorFindingRepository,
    DetectorIncidentRepository,
    DetectorRepository,
)
from apps.detectors.repositories.daily_sheet import DetectorDailySheetRepository
from apps.detectors.services.daily_sheet import texts
from apps.detectors.services.daily_sheet.config import (
    ATTENTION_SIZE,
    CATEGORY_LABELS,
    CONFIG_VERSION,
    NORMALIZED_TAIL_DAYS,
    PLAN_GRACE_DAYS,
    REPAIRS_LOOKBACK_DAYS,
    REPEAT_LOOKBACK_DAYS,
    SEVERITY_RANK,
    SEVERITY_STRONG,
    TELEMETRY_FRESH_DAYS,
    TOP_SIZE,
    sheet_applies,
)
from apps.detectors.services.daily_sheet.context import (
    RateSnapshot,
    RepairInfo,
    WellContext,
)
from apps.detectors.services.daily_sheet.errors import (
    DailySheetDataNotReadyError,
    DailySheetDateInFutureError,
    DailySheetDetectorNotFoundError,
    DailySheetNotApplicableError,
)
from apps.detectors.services.daily_sheet.oil_fields import (
    OilFieldRef,
    prefixes_key,
    well_matches,
)
from apps.detectors.services.daily_sheet.render import render_docx
from apps.detectors.services.daily_sheet.selection import (
    Episode,
    category_key,
    day_bounds,
    episodes_on_date,
    group_by_well,
)
from apps.files.services.file import FileService
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.models.tech_regime import TechRegime
from apps.telemetry.models.telemetry import Telemetry
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.well_rates import water_cut
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.s3.storage import AiobotoFileStorage

logger = get_logger(__name__)
settings = get_settings()

_ERROR_MAX_CHARS = 1000


@dataclass(frozen=True, slots=True)
class SheetTarget:
    detector_code: str
    # Локальный org.id НГДУ (как в остальном API) и его ABAI id для станций.
    ngdu_id: int
    ngdu_name: str
    abai_ngdu_id: int
    sheet_date: date
    # Фильтр по месторождениям; пустой кортеж — весь НГДУ.
    oil_fields: tuple[OilFieldRef, ...] = ()

    @property
    def prefixes(self) -> tuple[str, ...]:
        return tuple(field.prefix for field in self.oil_fields)

    @property
    def prefixes_key(self) -> str:
        return prefixes_key(self.oil_fields)


def local_now() -> datetime:
    """Наивное местное время — в том же виде, что метки эпизодов и телеметрии."""
    return datetime.now(settings.ZONE_INFO).replace(tzinfo=None)


def file_key(sheet: DailySheetDTO) -> str:
    """Ключ docx в бакете: версии за одну дату различаются временем сборки."""
    try:
        ngdu_code = AbaiNGDUIDsEnum(sheet.abai_ngdu_id).name
    except ValueError:
        ngdu_code = str(sheet.abai_ngdu_id)
    built = sheet.built_at or local_now()
    fields = "-".join(field.prefix for field in sheet.oil_fields)
    scope = f"{ngdu_code}_{fields}" if fields else ngdu_code
    name = f"Vedomost_{sheet.detector_code}_{scope}_{sheet.sheet_date:%Y-%m-%d}.docx"
    return (
        f"detectors/daily_sheet/{sheet.detector_code}/{sheet.abai_ngdu_id}/"
        f"{sheet.sheet_date:%Y-%m-%d}/{built:%Y%m%dT%H%M%S}_{name}"
    )


class DailySheetBuilder:
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
        self.detector_repo = DetectorRepository(session)
        self.incident_repo = DetectorIncidentRepository(session)
        self.cursor_repo = DetectorCursorRepository(session)
        self.conclusion_repo = DetectorConclusionRepository(session)
        self.finding_repo = DetectorFindingRepository(session)
        self.sheet_repo = DetectorDailySheetRepository(session)
        self.station_repo = SdmoStationRepository(session)
        self.fc_data_repo = SdmoFcDataRepository(session)
        self.well_repo = WellRepository(session)
        self.telemetry_repo = TelemetryRepository(session)
        self.tech_regime_repo = TechRegimeRepository(session)
        self.repair_repo = RepairRepository(session)

    async def build(self, target: SheetTarget) -> DailySheetDTO:
        """Собрать, сохранить файл и строку; вернуть содержимое без ссылки."""
        today = self.now.date()
        if target.sheet_date > today:
            raise DailySheetDateInFutureError(
                details={"date": target.sheet_date.isoformat()},
            )
        detector = await self.detector_repo.get_by_code(target.detector_code)
        if detector is None:
            raise DailySheetDetectorNotFoundError(
                details={"detector_code": target.detector_code},
            )
        if not sheet_applies(target.detector_code, target.abai_ngdu_id):
            raise DailySheetNotApplicableError(
                details={
                    "detector_code": target.detector_code,
                    "ngdu_id": target.ngdu_id,
                },
            )

        day_start, day_end = day_bounds(target.sheet_date)
        # R10 считается по замерам ЦИТС (source=wincc), а не по станциям СДМО.
        by_cits = detector.source == "wincc"
        if by_cits:
            coverage = await self._cits_coverage(
                target,
                day_start=day_start,
                day_end=day_end,
                partial_day=target.sheet_date == today,
            )
        else:
            coverage = await self._coverage(
                target,
                day_end=day_end,
                partial_day=target.sheet_date == today,
            )
        if coverage.stations_reporting == 0:
            raise DailySheetDataNotReadyError(
                details={
                    "detector_code": target.detector_code,
                    "ngdu_id": target.ngdu_id,
                    "date": target.sheet_date.isoformat(),
                    "coverage": coverage.model_dump(),
                },
            )

        incidents = await self.incident_repo.list_for_ngdu_sheet(
            detector_code=target.detector_code,
            abai_ngdu_id=target.abai_ngdu_id,
            opened_before=day_end,
            normalized_since=day_start - timedelta(days=NORMALIZED_TAIL_DAYS),
            well_name_prefixes=target.prefixes or None,
        )
        episodes = episodes_on_date(incidents, sheet_date=target.sheet_date)
        contexts = await self._contexts(
            target,
            episodes,
            day_start=day_start,
            day_end=day_end,
        )
        sheet = self._compose(
            target,
            detector.name_ru,
            contexts,
            coverage,
            day_end=day_end,
        )
        if by_cits:
            sheet.measure_requests = await self._measure_requests(target)
        await self._store(sheet)
        logger.info(
            "Daily sheet %s ngdu=%s fields=%r date=%s: rows=%s file_id=%s",
            target.detector_code,
            target.abai_ngdu_id,
            target.prefixes_key,
            target.sheet_date,
            sheet.rows_count,
            sheet.file_id,
        )
        return sheet

    # --- охват ---------------------------------------------------------------

    async def _coverage(
        self,
        target: SheetTarget,
        *,
        day_end: datetime,
        partial_day: bool,
    ) -> DailySheetCoverageDTO:
        stations = await self.station_repo.list_by_ngdu(target.abai_ngdu_id)
        stations = [station for station in stations if station.well_id is not None]
        if target.prefixes:
            # Месторождение — по имени скважины, не по коду станции: коды
            # бывают другими (MLD_2631 у VMB_2631).
            wells = await self.well_repo.list_by_ids(
                [station.well_id for station in stations],
            )
            names = {well.id: well.name for well in wells}
            stations = [
                station
                for station in stations
                if well_matches(names.get(station.well_id, ""), target.prefixes)
            ]
        station_ids = [station.id for station in stations]
        reporting = await self.fc_data_repo.list_reporting_station_ids(
            station_ids,
            day=target.sheet_date,
        )
        cursors = (
            await self.cursor_repo.get_map(target.detector_code, station_ids)
            if station_ids
            else {}
        )
        processed = (
            sum(1 for cursor in cursors.values() if cursor.last_event_at >= day_end)
            if cursors
            else None
        )
        return DailySheetCoverageDTO(
            stations_total=len(station_ids),
            stations_reporting=len(reporting),
            stations_processed=processed,
            partial_day=partial_day,
        )

    async def _cits_coverage(
        self,
        target: SheetTarget,
        *,
        day_start: datetime,
        day_end: datetime,
        partial_day: bool,
    ) -> DailySheetCoverageDTO:
        """Охват по замерам ЦИТС: фонд правила — скважины НГДУ с замерами за его
        окно, из них с замером за сутки; курсор правила — один на НГДУ."""
        fund = await self.telemetry_repo.list_well_ids_with_rows(
            target.abai_ngdu_id,
            since=day_end - timedelta(days=CITS_WINDOW_D),
            until=day_end,
        )
        if target.prefixes:
            wells = await self.well_repo.list_by_ids(list(fund))
            fund = {
                well.id for well in wells if well_matches(well.name, target.prefixes)
            }
        reporting = fund & await self.telemetry_repo.list_well_ids_with_rows(
            target.abai_ngdu_id,
            since=day_start,
            until=day_end,
        )
        cursors = await self.cursor_repo.get_map(
            target.detector_code,
            [target.abai_ngdu_id],
        )
        cursor = cursors.get(target.abai_ngdu_id)
        processed = (
            None
            if cursor is None
            else (len(fund) if cursor.last_event_at >= day_end else 0)
        )
        return DailySheetCoverageDTO(
            source="cits",
            stations_total=len(fund),
            stations_reporting=len(reporting),
            stations_processed=processed,
            partial_day=partial_day,
        )

    async def _measure_requests(self, target: SheetTarget) -> list[str]:
        """R10: «замер устарел» за дату — по строке на скважину, давние сверху."""
        findings = await self.finding_repo.list_for_date(
            detector_code=target.detector_code,
            fix_date=target.sheet_date,
            kinds=[FINDING_STALE],
        )
        if not findings:
            return []
        wells = await self.well_repo.list_by_ids([f.well_id for f in findings])
        names = {well.id: well.name for well in wells}
        items = [
            (names.get(f.well_id, f"id:{f.well_id}"), f.payload or {})
            for f in findings
            if not target.prefixes
            or well_matches(names.get(f.well_id, ""), target.prefixes)
        ]
        items.sort(key=lambda item: -(item[1].get("age_d") or 0))
        return [texts.measure_request_text(name, payload) for name, payload in items]

    # --- обогащение ----------------------------------------------------------

    async def _contexts(
        self,
        target: SheetTarget,
        episodes: Sequence[Episode],
        *,
        day_start: datetime,
        day_end: datetime,
    ) -> list[WellContext]:
        grouped = group_by_well(episodes)
        if not grouped:
            return []
        well_ids = list(grouped)
        wells = {well.id: well for well in await self.well_repo.list_by_ids(well_ids)}
        well_by_abai = {well.abai_id: well.id for well in wells.values()}
        abai_ids = list(well_by_abai)

        previous_by_well = await self._previous_episodes(
            target,
            well_ids,
            scope_ids={episode.incident.id for episode in episodes},
            day_start=day_start,
            day_end=day_end,
        )
        conclusions = await self._conclusions(
            [grouped[well_id][0].incident.id for well_id in well_ids],
        )
        last_rows = await self.telemetry_repo.get_last_by_well_ids_before(
            well_ids,
            before=day_end,
        )
        regimes = await self.tech_regime_repo.get_current_by_abai_well_ids(
            abai_ids,
            on_date=target.sheet_date,
            grace_days=PLAN_GRACE_DAYS,
        )
        repairs_by_well = await self._repairs(
            well_ids,
            well_by_abai,
            sheet_date=target.sheet_date,
        )

        contexts = []
        for well_id in well_ids:
            well_episodes = grouped[well_id]
            primary = well_episodes[0]
            well = wells.get(well_id)
            abai_id = well.abai_id if well is not None else None
            conclusion = conclusions.get((primary.incident.id, primary.level))
            contexts.append(
                WellContext(
                    detector_code=target.detector_code,
                    well_id=well_id,
                    well_name=well.name if well is not None else f"id:{well_id}",
                    episodes=well_episodes,
                    previous=previous_by_well.get(well_id, []),
                    rates=self._rates(
                        last_rows.get(well_id),
                        regimes.get(abai_id) if abai_id is not None else None,
                        day_end=day_end,
                    ),
                    repairs=repairs_by_well.get(well_id, []),
                    **self._verdict(primary, conclusion),
                ),
            )
        return contexts

    async def _previous_episodes(
        self,
        target: SheetTarget,
        well_ids: Sequence[int],
        *,
        scope_ids: set[int],
        day_start: datetime,
        day_end: datetime,
    ) -> dict[int, list[DetectorIncident]]:
        history = await self.incident_repo.list_history_by_well_ids(
            detector_code=target.detector_code,
            well_ids=well_ids,
            opened_since=day_start - timedelta(days=REPEAT_LOOKBACK_DAYS),
            opened_before=day_end,
        )
        previous: dict[int, list[DetectorIncident]] = {}
        for incident in history:
            if incident.id not in scope_ids and incident.normalized_at is not None:
                previous.setdefault(incident.well_id, []).append(incident)
        return previous

    async def _conclusions(
        self,
        incident_ids: Sequence[int],
    ) -> dict[tuple[int, str], DetectorConclusion]:
        """Заключение по (эпизод, уровень); completed важнее pending/failed."""
        picked: dict[tuple[int, str], DetectorConclusion] = {}
        for conclusion in await self.conclusion_repo.list_by_incident_ids(
            incident_ids,
            status=None,
        ):
            key = (conclusion.incident_id, conclusion.level)
            current = picked.get(key)
            if current is None or (
                current.status != CONCLUSION_STATUS_COMPLETED
                and conclusion.status == CONCLUSION_STATUS_COMPLETED
            ):
                picked[key] = conclusion
        return picked

    async def _repairs(
        self,
        well_ids: Sequence[int],
        well_by_abai: dict[int, int],
        *,
        sheet_date: date,
    ) -> dict[int, list[RepairInfo]]:
        repairs = await self.repair_repo.list_covering_range(
            well_ids,
            list(well_by_abai),
            date_from=sheet_date - timedelta(days=REPAIRS_LOOKBACK_DAYS),
            date_to=sheet_date,
        )
        known = set(well_ids)
        by_well: dict[int, list[RepairInfo]] = {}
        for repair in repairs:
            well_id = (
                repair.well_id
                if repair.well_id in known
                else well_by_abai.get(repair.abai_well_id)
            )
            if well_id is None:
                continue
            by_well.setdefault(well_id, []).append(
                RepairInfo(start=repair.start_time, end=repair.end_time),
            )
        return by_well

    @staticmethod
    def _rates(
        last: Telemetry | None,
        regime: TechRegime | None,
        *,
        day_end: datetime,
    ) -> RateSnapshot:
        snapshot = RateSnapshot(
            plan_liquid=regime.liquid if regime is not None else None,
            plan_oil=regime.oil if regime is not None else None,
        )
        if last is not None:
            snapshot.liquid, snapshot.oil = last.qv_liquid, last.qm_oil
            snapshot.water_cut = water_cut(
                liquid_rate=last.qv_liquid,
                oil_rate=last.qm_oil,
            )
            snapshot.measured_at = last.date_time
            snapshot.stale = last.date_time < day_end - timedelta(
                days=TELEMETRY_FRESH_DAYS,
            )
        return snapshot

    @staticmethod
    def _verdict(
        primary: Episode,
        conclusion: DetectorConclusion | None,
    ) -> dict:
        """Причина и уверенность: из заключения того же уровня, иначе из
        справочника — тем же кодом, что и генератор заключений."""
        incident = primary.incident
        if conclusion is not None:
            return {
                "confidence": conclusion.confidence,
                "cause": conclusion.cause,
            }
        return {
            "confidence": catalog.compute_confidence(
                incident.detector_code,
                incident.payload,
            ),
            "cause": catalog.cause_for(
                incident.detector_code,
                incident.reason_code,
                primary.level,
                incident.payload,
            )
            or incident.reason_code,
        }

    # --- строки и сохранение ---------------------------------------------------

    def _compose(
        self,
        target: SheetTarget,
        detector_name: str | None,
        contexts: Sequence[WellContext],
        coverage: DailySheetCoverageDTO,
        *,
        day_end: datetime,
    ) -> DailySheetDTO:
        ordered = sorted(
            contexts,
            key=lambda ctx: (
                -ctx.probability_percent,
                SEVERITY_RANK[ctx.severity],
                not ctx.primary.is_active,
                -ctx.primary.incident.opened_at.timestamp(),
            ),
        )
        rows = [
            DailySheetRowDTO(
                number=number,
                well_id=ctx.well_id,
                well_name=ctx.well_name,
                category=CATEGORY_LABELS.get(category_key(ctx.rates.plan_oil) or ""),
                detected_at=ctx.primary.incident.detected_at,
                deviation=texts.deviation_text(
                    ctx,
                    day_end=day_end,
                    partial_day=coverage.partial_day,
                ),
                cause=texts.cause_text(ctx),
                probability_percent=ctx.probability_percent,
                rates=texts.rates_text(ctx),
                plan_oil=texts.plan_oil_text(ctx),
                incident_ids=[episode.incident.id for episode in ctx.episodes],
                level=ctx.primary.level,
                status=ctx.primary.status,
                severity=ctx.severity,
            )
            for number, ctx in enumerate(ordered, start=1)
        ]
        top = [
            DailySheetTopItemDTO(
                rank=rank,
                well_name=ctx.well_name,
                probability_percent=ctx.probability_percent,
                text=texts.top_text(ctx),
            )
            for rank, ctx in enumerate(ordered[:TOP_SIZE], start=1)
        ]
        attention = [
            texts.attention_text(ctx)
            for ctx in ordered[TOP_SIZE:]
            if ctx.primary.is_active and (ctx.severity == SEVERITY_STRONG or ctx.losses)
        ][:ATTENTION_SIZE]
        notes = []
        if coverage.partial_day:
            notes.append(
                "Ведомость за незавершённые сутки: эпизоды и дебиты могут "
                "измениться, утренняя сборка следующего дня её перезапишет.",
            )
        return DailySheetDTO(
            detector_code=target.detector_code,
            detector_name_ru=detector_name,
            ngdu_id=target.ngdu_id,
            ngdu_name=target.ngdu_name,
            abai_ngdu_id=target.abai_ngdu_id,
            sheet_date=target.sheet_date,
            oil_fields=[
                DailySheetOilFieldDTO(id=f.id, prefix=f.prefix, name=f.name)
                for f in target.oil_fields
            ],
            status=DAILY_SHEET_STATUS_COMPLETED,
            rows_count=len(rows),
            coverage=coverage,
            built_at=self.now,
            config_version=CONFIG_VERSION,
            file_id=None,
            rebuilt=True,
            top=top,
            attention=attention,
            rows=rows,
            notes=notes,
        )

    async def _store(self, sheet: DailySheetDTO) -> None:
        """Файл в S3 (FileService сам коммитит строку файла), затем строка ведомости.

        Неудача рендера или загрузки фиксируется строкой ``failed`` с ошибкой,
        чтобы утренний проход не считал дату успешно собранной.
        """
        base = {
            "detector_code": sheet.detector_code,
            "abai_ngdu_id": sheet.abai_ngdu_id,
            "sheet_date": sheet.sheet_date,
            "oil_field_prefixes": prefixes_key(
                [OilFieldRef(f.id, f.prefix, f.name) for f in sheet.oil_fields],
            ),
            "rows_count": sheet.rows_count,
            "coverage": sheet.coverage.model_dump() if sheet.coverage else None,
            "built_at": sheet.built_at,
            "config_version": sheet.config_version,
        }
        try:
            buffer = render_docx(sheet)
            db_file = await FileService(self.session, self.storage).upload(
                buffer,
                file_key(sheet),
            )
            sheet.file_id = db_file.id
            await self.sheet_repo.upsert(
                UpsertDailySheetDTO(
                    **base,
                    status=DAILY_SHEET_STATUS_COMPLETED,
                    file_id=db_file.id,
                    content=sheet.model_dump(
                        mode="json",
                        include={
                            "oil_fields",
                            "top",
                            "attention",
                            "rows",
                            "measure_requests",
                            "notes",
                        },
                    ),
                ),
            )
            await self.session.commit()
        except Exception as exc:
            logger.exception(
                "Daily sheet %s ngdu=%s date=%s failed to store",
                sheet.detector_code,
                sheet.abai_ngdu_id,
                sheet.sheet_date,
            )
            await self.session.rollback()
            await self.sheet_repo.upsert(
                UpsertDailySheetDTO(
                    **base,
                    status=DAILY_SHEET_STATUS_FAILED,
                    error=repr(exc)[:_ERROR_MAX_CHARS],
                ),
            )
            await self.session.commit()
            raise
