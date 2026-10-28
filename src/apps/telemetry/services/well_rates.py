"""Дебиты скважины (факт + план) — один ответ для карточки и для матрицы."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final

from apps.telemetry.dto.internal.well_rates import WellRatesDTO
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from core.settings import get_settings

# Замер дебита идёт не каждый день (в среднем раз в 2–3 суток); замер старше
# стольких суток не отражает текущую добычу. Старше 7 суток на 28.10.2026:
# ДМГ 6 %, ЖМГ 5 %, VMB 9 %, ЖлМГ 28 % скважин с планом.
FRESH_TELEMETRY_DAYS: Final = 7

# Границы обводнённости: значение вне 0…100 означает мусор в дебитах, а не
# реальный режим (в telemetry_well встречаются выбросы на много порядков).
WATER_CUT_MIN = 0.0
WATER_CUT_MAX = 100.0


def oil_rate_deviation(
    *,
    oil_rate: float | None,
    plan_oil_rate: float | None,
) -> tuple[float | None, float | None]:
    """(факт − план, т/сут; то же в % к плану) — знак минус означает потерю.

    Без замера или плана отклонение не определено; при нулевом плане процент
    не определён, а разница есть (вся добыча сверх режима).
    """
    if oil_rate is None or plan_oil_rate is None:
        return None, None
    delta = oil_rate - plan_oil_rate
    if plan_oil_rate <= 0:
        return round(delta, 1), None
    # Процент — от неокруглённой разницы, иначе 0 против плана 1.99 даёт −100.5 %.
    return round(delta, 1), round(delta / plan_oil_rate * 100, 1)


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
        *,
        now: datetime | None = None,
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

        # Время замеров ЦИТС — наивное местное.
        now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        fresh_since = now - timedelta(days=FRESH_TELEMETRY_DAYS)
        rates = {}
        for well_id, abai_well_id in abai_id_by_well_id.items():
            last = telemetry.get(well_id)
            regime = regimes.get(abai_well_id)
            oil_rate = last.qm_oil if last else None
            liquid_rate = last.qv_liquid if last else None
            plan_oil_rate = regime.oil if regime else None
            deviation, deviation_percent = oil_rate_deviation(
                oil_rate=oil_rate,
                plan_oil_rate=plan_oil_rate,
            )
            rates[well_id] = WellRatesDTO(
                oil_rate=oil_rate,
                liquid_rate=liquid_rate,
                water_cut=water_cut(liquid_rate=liquid_rate, oil_rate=oil_rate),
                telemetry_time=last.date_time if last else None,
                telemetry_stale=last is None or last.date_time < fresh_since,
                plan_oil_rate=plan_oil_rate,
                plan_liquid_rate=regime.liquid if regime else None,
                tech_regime_date=regime.start_date if regime else None,
                oil_rate_deviation=deviation,
                oil_rate_deviation_percent=deviation_percent,
            )
        return rates
