"""Дебиты скважины (факт + план) — один ответ для карточки и для матрицы."""

from collections.abc import Mapping

from apps.telemetry.dto.internal.well_rates import WellRatesDTO
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository

# Границы обводнённости: значение вне 0…100 означает мусор в дебитах, а не
# реальный режим (в telemetry_well встречаются выбросы на много порядков).
WATER_CUT_MIN = 0.0
WATER_CUT_MAX = 100.0


def water_cut(*, liquid_rate: float | None, oil_rate: float | None) -> float | None:
    """Обводнённость, % = доля воды в жидкости: (жидкость - нефть) / жидкость."""
    if liquid_rate is None or oil_rate is None or liquid_rate <= 0:
        return None

    value = (liquid_rate - oil_rate) / liquid_rate * 100
    return round(min(max(value, WATER_CUT_MIN), WATER_CUT_MAX), 1)


class WellRatesService:
    def __init__(
        self,
        telemetry_repository: TelemetryRepository,
        tech_regime_repository: TechRegimeRepository,
    ) -> None:
        self.telemetry_repository = telemetry_repository
        self.tech_regime_repository = tech_regime_repository

    async def get_for_well(
        self,
        *,
        well_id: int,
        abai_well_id: int,
    ) -> WellRatesDTO:
        rates = await self.get_for_wells({well_id: abai_well_id})
        return rates[well_id]

    async def get_for_wells(
        self,
        abai_id_by_well_id: Mapping[int, int],
    ) -> dict[int, WellRatesDTO]:
        """Ключ — локальный ``well_id``.

        Два разных ключа: факт лежит в ``telemetry_well`` по локальному id,
        план приезжает из ABAI и связан по ``abai_well_id``. В ответе есть
        строка на каждую запрошенную скважину — отсутствие данных это тоже
        ответ (все поля ``None``), и вызывающему не нужно отличать «нет ключа»
        от «нет замеров».
        """
        telemetry = await self.telemetry_repository.get_last_by_well_ids(
            list(abai_id_by_well_id),
        )
        regimes = await self.tech_regime_repository.get_last_by_abai_well_ids(
            list(abai_id_by_well_id.values()),
        )

        rates = {}
        for well_id, abai_well_id in abai_id_by_well_id.items():
            last = telemetry.get(well_id)
            regime = regimes.get(abai_well_id)
            oil_rate = last.qm_oil if last else None
            liquid_rate = last.qv_liquid if last else None
            rates[well_id] = WellRatesDTO(
                oil_rate=oil_rate,
                liquid_rate=liquid_rate,
                water_cut=water_cut(liquid_rate=liquid_rate, oil_rate=oil_rate),
                telemetry_time=last.date_time if last else None,
                plan_oil_rate=regime.oil if regime else None,
                plan_liquid_rate=regime.liquid if regime else None,
                tech_regime_date=regime.start_date if regime else None,
            )
        return rates
