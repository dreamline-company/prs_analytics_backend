# ruff: noqa: RUF001, RUF002
"""Computes and persists the KPI ПРС metrics for one repair.

A deterministic-plus-AI counterpart to :class:`AICoordinator`: it assembles the
six KPI ПРС cards for a repair from their real sources and upserts one
``repairs_repair_kpi`` row. Metric values live in the ``metrics`` JSON blob.

Sources per card:
  * Выход на тех. режим — план из ``TechRegime``, текущий замер из
    ``Telemetry``; выводим план/текущее/отклонение.
  * КПД насоса после ПРС — вердикт AI по динамограмме ПОСЛЕ ремонта.
  * Подтверждение операций ПОР — AI-сравнение отчёта (``RepairSummary``) с
    планом из документа ПОР (``por_file_id``); отклонение % + вердикт.
  * Анализ СПО — AI-сравнение отчёта с событиями СПО (``SPOEvent``);
    отклонение % + вердикт.
  * Нарушения ТБ — число error-screens бригады за интервал ремонта.
  * KPI динамограмм — оценки ДО/ПОСЛЕ + направление (лучше/хуже) и вердикт.

AI sub-verdicts (ПОР, СПО) are fingerprinted by source id + prompt version and
reused across pipeline passes so we don't re-bill the LLM every run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any

from markitdown import MarkItDown

from apps.repairs.dto.internal.repositories.kpi import (
    CreateRepairKPIDTO,
    UpdateRepairKPIDTO,
)
from apps.repairs.models.analytics import AI_STATUS_COMPLETED
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import FileNotExistError

from .por_confirmation_processor import PORConfirmationInput
from .spo_analysis_processor import SPOAnalysisInput

if TYPE_CHECKING:
    from apps.files.repositories.file import FileRepository
    from apps.org.repositories import UniqueBrigadeRepository
    from apps.repairs.models.analytics import RepairDynamogramAIResult, RepairKPI
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.analytics import RepairAnalyticsSPORepository
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.docs import RepairDocRepository
    from apps.repairs.repositories.kpi import RepairKPIRepository
    from apps.repairs.repositories.reports import RepairSummaryRepository
    from apps.telemetry.repositories.tech_regime import TechRegimeRepository
    from apps.telemetry.repositories.telemetry import TelemetryRepository
    from apps.wells.repositories.spo_event import SPOEventRepository
    from apps.wells.repositories.well import WellRepository
    from shared.database.s3.storage import AiobotoFileStorage
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

    from .por_confirmation_processor import PORConfirmationProcessor
    from .spo_analysis_processor import SPOAnalysisProcessor

logger = get_logger(__name__)
settings = get_settings()

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")
_TECH_REGIME_UNIT = "м3/сут"


@dataclass(slots=True)
class RepairKPICalculator:
    """Builds KPI ПРС metrics for a repair and stores them."""

    kpi_repo: RepairKPIRepository
    well_repo: WellRepository
    summary_repo: RepairSummaryRepository
    doc_repo: RepairDocRepository
    analytics_spo_repo: RepairAnalyticsSPORepository
    spo_event_repo: SPOEventRepository
    file_repo: FileRepository
    storage: AiobotoFileStorage
    tech_regime_repo: TechRegimeRepository
    telemetry_repo: TelemetryRepository
    repair_brigade_repo: RepairBrigadeRepository
    unique_brigade_repo: UniqueBrigadeRepository
    cm_brigade_repo: CMBrigadeRepository
    cm_brigade_error_screen_repo: CMBrigadeErrorScreenRepository
    por_processor: PORConfirmationProcessor
    spo_analysis_processor: SPOAnalysisProcessor

    async def compute_and_store(
        self,
        *,
        analytics_id: int,
        repair: Repair,
        dynamogram_before_ai: RepairDynamogramAIResult | None,
        dynamogram_after_ai: RepairDynamogramAIResult | None,
    ) -> RepairKPI:
        computed_at = datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        existing = await self.kpi_repo.get_by_analytics_id(analytics_id)
        prev = existing.metrics if existing and existing.metrics else {}

        report_items = await self._report_items(repair.id)

        metrics = {
            "tech_regime": await self._tech_regime(repair),
            "pump_efficiency": self._pump_efficiency(dynamogram_after_ai),
            "por_confirmation": await self._por_confirmation(
                repair,
                report_items,
                prev.get("por_confirmation"),
            ),
            "spo_analysis": await self._spo_analysis(
                analytics_id,
                repair,
                report_items,
                prev.get("spo_analysis"),
            ),
            "safety_violations": {"count": await self._count_violations(repair)},
            "dynamogram_kpi": self._dynamogram_kpi(
                dynamogram_before_ai,
                dynamogram_after_ai,
            ),
        }
        logger.info("Repair id=%s KPI computed: %s", repair.id, metrics)

        if existing is None:
            return await self.kpi_repo.create(
                CreateRepairKPIDTO(
                    analytics_id=analytics_id,
                    status=AI_STATUS_COMPLETED,
                    metrics=metrics,
                    computed_at=computed_at,
                ),
            )
        return await self.kpi_repo.update_by_analytics_id(
            analytics_id=analytics_id,
            data=UpdateRepairKPIDTO(
                status=AI_STATUS_COMPLETED,
                metrics=metrics,
                error=None,
                computed_at=computed_at,
            ),
        )

    # --- Выход на тех. режим -------------------------------------------------

    async def _tech_regime(self, repair: Repair) -> dict[str, Any]:
        plan = await self._tech_regime_plan(repair.abai_well_id)
        current = await self._tech_regime_current(repair)
        deviation = None
        if plan not in (None, 0) and current is not None:
            deviation = round((current - plan) / plan * 100, 2)
        return {
            "plan": plan,
            "current": current,
            "deviation_pct": deviation,
            "unit": _TECH_REGIME_UNIT,
        }

    async def _tech_regime_plan(self, abai_well_id: int | None) -> float | None:
        if abai_well_id is None:
            return None
        regimes = await self.tech_regime_repo.list_by_abai_well_id(abai_well_id)
        return regimes[0].liquid if regimes else None

    async def _tech_regime_current(self, repair: Repair) -> float | None:
        # Repair.well_id is not populated — resolve the well via abai_well_id.
        if repair.abai_well_id is None:
            return None
        well = await self.well_repo.get_by_abai_id(abai_id=repair.abai_well_id)
        if well is None:
            return None
        measurements = await self.telemetry_repo.list_by_well_id_in_period(
            well.id,
            date_time_from=repair.end_time,
        )
        if not measurements:
            return None
        return measurements[-1].qv_liquid

    # --- КПД насоса после ПРС ------------------------------------------------

    def _pump_efficiency(
        self,
        dynamogram_after_ai: RepairDynamogramAIResult | None,
    ) -> dict[str, Any]:
        parsed = self._parsed(dynamogram_after_ai)
        return {
            "value_pct": self._num(
                parsed,
                "pump_efficiency_pct",
                "pump_efficiency",
                "kpd",
            ),
            "verdict": self._text(parsed, "verdict", "condition"),
        }

    # --- Подтверждение операций ПОР -----------------------------------------

    async def _por_confirmation(
        self,
        repair: Repair,
        report_items: list[str],
        prev: dict | None,
    ) -> dict[str, Any]:
        doc = await self.doc_repo.get_by_repair_id(repair.id)
        por_file_id = doc.por_file_id if doc else None
        version = self.por_processor.prompt_version
        if por_file_id is None:
            return self._empty_block("Документ ПОР отсутствует.", version=version)
        reused = self._reuse(prev, version=version, source=por_file_id)
        if reused is not None:
            return reused

        por_text = await self._por_text(por_file_id)
        if por_text is None:
            return self._empty_block(
                "Документ ПОР не распознан.",
                version=version,
            )
        result = await self.por_processor.process(
            PORConfirmationInput(
                repair_id=repair.id,
                por_text=por_text,
                report_items=report_items,
            ),
        )
        return self._deviation_block(result, version=version, source=por_file_id)

    async def _por_text(self, por_file_id: int | None) -> str | None:
        if por_file_id is None:
            return None
        file_row = await self.file_repo.get_by_id(por_file_id)
        s3_key = file_row.file if file_row is not None else None
        if not s3_key:
            return None
        payload = await self._download_bytes(s3_key)
        if payload is None:
            return None
        return self._pdf_to_text(payload)

    # --- Анализ спуско-подъёмных операций ------------------------------------

    async def _spo_analysis(
        self,
        analytics_id: int,
        repair: Repair,
        report_items: list[str],
        prev: dict | None,
    ) -> dict[str, Any]:
        link = await self.analytics_spo_repo.get_by_analytics_id(analytics_id)
        spo_id = link.spo_id if link else None
        version = self.spo_analysis_processor.prompt_version
        if spo_id is None:
            return self._empty_block("СПО не привязано к ремонту.", version=version)
        reused = self._reuse(prev, version=version, source=spo_id)
        if reused is not None:
            return reused

        rows = await self.spo_event_repo.list_by_spo_id(spo_id)
        events = [f"{r.time_text or ''} {r.text}".strip() for r in rows]
        if not events:
            return self._empty_block("События СПО отсутствуют.", version=version)
        result = await self.spo_analysis_processor.process(
            SPOAnalysisInput(
                repair_id=repair.id,
                spo_events=events,
                report_items=report_items,
            ),
        )
        return self._deviation_block(result, version=version, source=spo_id)

    # --- KPI динамограмм -----------------------------------------------------

    def _dynamogram_kpi(
        self,
        dynamogram_before_ai: RepairDynamogramAIResult | None,
        dynamogram_after_ai: RepairDynamogramAIResult | None,
    ) -> dict[str, Any]:
        before = self._parsed(dynamogram_before_ai)
        after = self._parsed(dynamogram_after_ai)
        before_pct = self._num(before, "score", "pump_efficiency_pct")
        after_pct = self._num(after, "score", "pump_efficiency_pct")

        if before_pct is None or after_pct is None:
            direction = None
            verdict = "Недостаточно данных для сравнения ДО/ПОСЛЕ."
        elif after_pct > before_pct:
            direction = "better"
            verdict = (
                f"Оценка выросла с {before_pct}% до {after_pct}% — "
                "состояние улучшилось."
            )
        elif after_pct < before_pct:
            direction = "worse"
            verdict = (
                f"Оценка снизилась с {before_pct}% до {after_pct}% — "
                "состояние ухудшилось."
            )
        else:
            direction = "same"
            verdict = f"Оценка без изменений ({after_pct}%)."
        return {
            "before_pct": before_pct,
            "after_pct": after_pct,
            "direction": direction,
            "verdict": verdict,
        }

    # --- Нарушения ТБ --------------------------------------------------------

    async def _count_violations(self, repair: Repair) -> int:
        link = await self.repair_brigade_repo.get_by_repair_id(repair.id)
        if link is None:
            return 0
        brigade = await self.unique_brigade_repo.get_by_id(link.brigade_id)
        if brigade is None:
            return 0
        match = _BRIGADE_NUMBER_RE.search(brigade.name)
        if match is None:
            return 0
        cm_brigades = await self.cm_brigade_repo.list_by_name(match.group(1))
        cm_ids = [cm.id for cm in cm_brigades]
        if not cm_ids:
            return 0
        end_time = repair.end_time or datetime.now()  # noqa: DTZ005
        screens = await self.cm_brigade_error_screen_repo.list_by_brigade_ids_in_range(
            cm_ids,
            start_time=repair.start_time,
            end_time=end_time,
        )
        return len(screens)

    # --- helpers -------------------------------------------------------------

    async def _report_items(self, repair_id: int) -> list[str]:
        summaries = await self.summary_repo.list_by_repair_id(repair_id)
        return [
            str(item)
            for summary in summaries
            for item in (summary.shift_details or [])
        ]

    @staticmethod
    def _empty_block(reason: str, *, version: str) -> dict[str, Any]:
        return {
            "deviation_pct": None,
            "verdict": reason,
            "_version": version,
            "_source": None,
        }

    @staticmethod
    def _deviation_block(
        result: Any,  # noqa: ANN401 — AIProcessingResult
        *,
        version: str,
        source: int | None,
    ) -> dict[str, Any]:
        parsed = RepairKPICalculator._parsed(result)
        return {
            "deviation_pct": RepairKPICalculator._num(parsed, "deviation_pct"),
            "verdict": RepairKPICalculator._text(parsed, "verdict"),
            "_version": version,
            "_source": source,
        }

    @staticmethod
    def _reuse(
        prev: dict | None,
        *,
        version: str,
        source: int | None,
    ) -> dict | None:
        """Reuse a stored AI sub-verdict if source + prompt version are unchanged."""
        if (
            source is not None
            and isinstance(prev, dict)
            and prev.get("_version") == version
            and prev.get("_source") == source
            and prev.get("deviation_pct") is not None
        ):
            return prev
        return None

    async def _download_bytes(self, s3_key: str) -> bytes | None:
        try:
            buf = await self.storage.download_file(s3_key)
        except FileNotExistError:
            logger.warning("S3 object missing for KPI ПОР input: %s", s3_key)
            return None
        return buf.getvalue()

    @staticmethod
    def _pdf_to_text(payload: bytes) -> str | None:
        try:
            result = MarkItDown().convert_stream(
                BytesIO(payload),
                file_extension=".pdf",
            )
        except Exception:
            logger.exception("Failed to convert ПОР PDF to text.")
            return None
        text = (result.text_content or "").strip()
        return text or None

    @staticmethod
    def _parsed(row: Any) -> dict | None:  # noqa: ANN401
        result = getattr(row, "result", None)
        if not isinstance(result, dict):
            return None
        parsed = result.get("parsed")
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _num(source: dict | None, *keys: str) -> float | None:
        if not source:
            return None
        for key in keys:
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    @staticmethod
    def _text(source: dict | None, *keys: str) -> str | None:
        if not source:
            return None
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
