"""Сводка по НГДУ: фонд, добыча нефти, выполнение плана, отклонения.

Все четыре показателя считаются на одном наборе скважин — всех НГДУ, одного
НГДУ или одного месторождения (см. ``NGDUWellsService.list_wells``) — и из
тех же источников, что паспорт матрицы: факт из
``telemetry_well``, план из техрежима ABAI, статус станции из СДМО, уровень
эпизодов детекторов — как ``incident_status.level`` в матрице инцидентов.
Свёртка чисел вынесена в чистую функцию ``summarize`` — правила порогов
проверяются без базы.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from apps.detectors.models.incident import INCIDENT_LEVEL_ALARM
from apps.detectors.services import WellIncidentStatusService
from apps.kpi.dto.internal.ngdu_summary import (
    NgduSummaryDeviationsDTO,
    NgduSummaryDTO,
    NgduSummaryOilDTO,
    NgduSummaryPlanDTO,
    NgduSummaryWellsDTO,
)
from apps.kpi.dto.queries.ngdu_summary import GetNgduSummaryQuery
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.services import NGDUWellsService

# Замер дебита идёт не каждый день (в среднем раз в 2–3 суток), поэтому
# «текущая добыча» — сумма последних замеров не старше этого окна; более
# старый замер считается отсутствующим, а не нулевым.
FRESH_TELEMETRY_DAYS: Final = 7
# Техрежимы месячные и приезжают с лагом: пока нового нет, план берётся из
# режима, закончившегося не раньше чем столько дней назад.
PLAN_GRACE_DAYS: Final = 31
# Недобор (losses) — по скважинам, где факт ниже плана больше чем на эту долю.
DEVIATION_THRESHOLD: Final = 0.10
# Регистр 1999 «Статус (VLT SALT)»: 1 — станция онлайн.
VLT_ONLINE: Final = 1


@dataclass(frozen=True, slots=True)
class WellSummaryInput:
    """Вход свёртки по одной скважине; ``None`` — данных нет."""

    well_id: int
    oil_fact: float | None  # свежий замер, т/сут
    oil_plan: float | None  # действующий техрежим, т/сут
    is_active: bool  # станция СДМО онлайн
    # Худший уровень активных эпизодов детекторов — alarm (как в матрице).
    is_alarm: bool = False


def summarize(rows: Sequence[WellSummaryInput], *, as_of: datetime) -> NgduSummaryDTO:
    """Свернуть скважины в четыре показателя.

    Добыча — по всем скважинам со свежим замером. План/факт и недобор —
    только по скважинам, где есть и свежий замер, и план больше нуля:
    скважина без замера не считается ни выполняющей план, ни отклонившейся,
    её состояние неизвестно. Скважины с отклонениями — те, у которых в
    матрице инцидентов уровень alarm.
    """
    measured = [row for row in rows if row.oil_fact is not None]
    comparable = [
        row for row in measured if row.oil_plan is not None and row.oil_plan > 0
    ]
    fact_sum = sum(row.oil_fact for row in comparable)  # type: ignore[misc]
    plan_sum = sum(row.oil_plan for row in comparable)  # type: ignore[misc]
    deviating = [
        row
        for row in comparable
        if row.oil_fact < row.oil_plan * (1 - DEVIATION_THRESHOLD)  # type: ignore[operator]
    ]
    losses = sum(row.oil_plan - row.oil_fact for row in deviating)  # type: ignore[operator]

    return NgduSummaryDTO(
        as_of=as_of,
        wells=NgduSummaryWellsDTO(
            total=len(rows),
            active=sum(1 for row in rows if row.is_active),
        ),
        oil_production=NgduSummaryOilDTO(
            value=round(sum(row.oil_fact for row in measured), 1),  # type: ignore[misc]
            wells_measured=len(measured),
            fresh_days=FRESH_TELEMETRY_DAYS,
        ),
        plan_fulfillment=NgduSummaryPlanDTO(
            percent=round(fact_sum / plan_sum * 100, 1) if plan_sum > 0 else None,
            fact=round(fact_sum, 1),
            plan=round(plan_sum, 1),
            wells=len(comparable),
        ),
        deviations=NgduSummaryDeviationsDTO(
            wells=sum(1 for row in rows if row.is_alarm),
            losses=round(losses, 1),
            threshold_percent=DEVIATION_THRESHOLD * 100,
        ),
    )


class GetNgduSummaryUseCase:
    def __init__(
        self,
        *,
        ngdu_wells_service: NGDUWellsService,
        telemetry_repository: TelemetryRepository,
        tech_regime_repository: TechRegimeRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
        well_incident_status_service: WellIncidentStatusService,
    ) -> None:
        self.ngdu_wells_service = ngdu_wells_service
        self.telemetry_repository = telemetry_repository
        self.tech_regime_repository = tech_regime_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository
        self.well_incident_status_service = well_incident_status_service

    async def execute(
        self,
        query: GetNgduSummaryQuery,
        *,
        now: datetime | None = None,
    ) -> NgduSummaryDTO:
        """``now`` — наивный UTC, как время в БД; параметр для воспроизводимых
        расчётов на исторических данных."""
        now = now or datetime.now(UTC).replace(tzinfo=None)
        wells = await self.ngdu_wells_service.list_wells(
            query.ngdu_id,
            oil_field_id=query.oil_field_id,
        )
        if not wells:
            return summarize([], as_of=now)

        well_ids = [well.id for well in wells]
        telemetry = await self.telemetry_repository.get_last_by_well_ids(well_ids)
        regimes = await self.tech_regime_repository.get_current_by_abai_well_ids(
            [well.abai_id for well in wells],
            on_date=now.date(),
            grace_days=PLAN_GRACE_DAYS,
        )
        vlt_statuses = (
            await self.sdmo_fc_data_repository.get_last_vlt_status_by_well_ids(
                well_ids,
            )
        )
        incident_statuses = await self.well_incident_status_service.get_for_wells(
            well_ids,
        )

        fresh_since = now - timedelta(days=FRESH_TELEMETRY_DAYS)
        rows = []
        for well in wells:
            last = telemetry.get(well.id)
            fresh = last is not None and last.date_time >= fresh_since
            regime = regimes.get(well.abai_id)
            rows.append(
                WellSummaryInput(
                    well_id=well.id,
                    oil_fact=last.qm_oil if fresh and last.qm_oil is not None else None,
                    oil_plan=regime.oil if regime is not None else None,
                    is_active=vlt_statuses.get(well.id) == VLT_ONLINE,
                    is_alarm=incident_statuses[well.id].level == INCIDENT_LEVEL_ALARM,
                ),
            )
        return summarize(rows, as_of=now)
