"""Входные данные расчёта МРП из базы: ремонты, статусы «в работе», способ добычи.

Время: ремонты ABAI и статусы (``wells_well_status``) записаны в одной шкале —
местном наивном времени, поэтому статусы берутся как есть, без перевода из UTC.

Фонд месяца — как в ABAI: категория OIL и статус «в работе» / «простой» /
«периодическая» на 1-е число месяца (Кайнар на 01.11.2026 — 531 скважина,
сверено с аналитиком заказчика).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from apps.compensation.constants import PLAN_GRACE_DAYS
from apps.org.services import well_name_prefix
from apps.repairs.services.mrp import Interval, RepairSpan, WellFundInput
from apps.repairs.services.repair_classifier import is_gtm_repair, is_technical_repair

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apps.repairs.repositories.repair import RepairRepository
    from apps.telemetry.models.tech_regime import TechRegime
    from apps.telemetry.repositories.tech_regime import TechRegimeRepository
    from apps.wells.models.well import Well
    from apps.wells.repositories.category_geo import (
        WellCategoryRepository,
        WellGeoRepository,
    )
    from apps.wells.repositories.well_expl import WellExplRepository
    from apps.wells.repositories.well_status import WellStatusRepository

# Время работы — только сутки в статусе «в работе». Простой (DWN, в т.ч. ремонт
# ПРС/КРС с причиной) и периодическая эксплуатация (PEXP) временем работы не
# считаются: так считает заказчик (сверка с его формой МРП за август 2026:
# 177 135 сут по статусу WRK против 177 067 в его форме).
WORK_STATUS_CODES: Final = frozenset({"WRK"})
# Фонд на 1-е число: категория «нефтяная» и один из этих статусов (простой и
# периодическая — фонд, но временем работы не считаются).
FUND_CATEGORY_CODE: Final = "OIL"
FUND_STATUS_CODES: Final = frozenset({"WRK", "DWN", "PEXP"})
# Открытый интервал в ABAI — dend 3333-12-31; всё позже этого года — «не закрыт».
OPEN_END_YEAR: Final = 3000
NO_FIELD: Final = "—"


def _month_firsts(window_start: datetime, window_end: datetime) -> list[date]:
    """1-е числа всех месяцев окна [start, end)."""
    firsts: list[date] = []
    year, month = window_start.year, window_start.month
    while date(year, month, 1) < window_end.date():
        firsts.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004
    return firsts


def fund_months_of(
    month_firsts: Sequence[date],
    category_intervals: Sequence[tuple[datetime | None, datetime]],
    status_intervals: Sequence[tuple[datetime, datetime]],
) -> frozenset[tuple[int, int]]:
    """Месяцы, на 1-е число которых скважина в фонде.

    Нужны одновременно действующая категория OIL и фондовый статус:
    ``dbeg <= 1-е число 00:00 < dend``. ``dbeg`` категории NULL — «с
    незапамятных времён».
    """
    months: set[tuple[int, int]] = set()
    for first in month_firsts:
        moment = datetime.combine(first, datetime.min.time())
        in_category = any(
            (dbeg is None or dbeg <= moment) and moment < dend
            for dbeg, dend in category_intervals
        )
        if in_category and any(
            dbeg <= moment < dend for dbeg, dend in status_intervals
        ):
            months.add((first.year, first.month))
    return frozenset(months)


def repair_fund_months_of(
    month_firsts: Sequence[date],
    fund_on_firsts: frozenset[tuple[int, int]],
) -> frozenset[tuple[int, int]]:
    """Месяцы, ремонты которых учитываются: в фонде на 1-е число или на конец.

    ``fund_on_firsts`` — месяцы (1-е числа, включая 1-е число после окна, если
    оно наступило), на которые скважина в фонде. Месяц годится, если скважина в
    фонде на его 1-е число или на 1-е число следующего месяца.
    """
    months: set[tuple[int, int]] = set()
    for first in month_firsts:
        year, month = first.year, first.month
        nxt = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004
        if (year, month) in fund_on_firsts or nxt in fund_on_firsts:
            months.add((year, month))
    return frozenset(months)


class MrpInputsService:
    def __init__(  # noqa: PLR0913
        self,
        *,
        repair_repository: RepairRepository,
        status_repository: WellStatusRepository,
        expl_repository: WellExplRepository,
        regime_repository: TechRegimeRepository,
        category_repository: WellCategoryRepository,
        geo_repository: WellGeoRepository,
    ) -> None:
        self.repair_repository = repair_repository
        self.status_repository = status_repository
        self.expl_repository = expl_repository
        self.regime_repository = regime_repository
        self.category_repository = category_repository
        self.geo_repository = geo_repository

    async def load_horizons(
        self,
        abai_well_ids: Sequence[int],
        *,
        on_date: date,
    ) -> dict[int, list[str]]:
        """Горизонты скважин на дату: ABAI id скважины → названия по алфавиту."""
        return await self.geo_repository.list_horizon_names_by_abai_well_ids(
            abai_well_ids,
            on_date=on_date,
        )

    async def load_current_regimes(
        self,
        abai_well_ids: Sequence[int],
        *,
        on_date: date,
    ) -> dict[int, TechRegime]:
        """Плановый режим скважин на дату (жидкость, нефть, обводнённость)."""
        return await self.regime_repository.get_current_by_abai_well_ids(
            abai_well_ids,
            on_date=on_date,
            grace_days=PLAN_GRACE_DAYS,
        )

    async def load_fund_inputs(
        self,
        wells: Sequence[Well],
        *,
        window_start: datetime,
        window_end: datetime,
        now: datetime,
    ) -> list[WellFundInput]:
        """Вход расчёта по методике КМГ для скважин за окно [start, end).

        ``window_*`` и ``now`` — наивное местное время.
        """
        if not wells:
            return []
        abai_ids = [well.abai_id for well in wells]

        repairs_by_well: dict[int, list[RepairSpan]] = defaultdict(list)
        # Ремонты, начатые до окна, но идущие в нём, вычитают время работы
        for (
            repair,
            type_name,
        ) in await self.repair_repository.list_with_type_by_well_abai_ids(
            abai_ids,
            since=window_start,
        ):
            if repair.start_time >= window_end:
                continue
            repairs_by_well[repair.abai_well_id].append(
                RepairSpan(
                    id=repair.id,
                    start=repair.start_time,
                    end=repair.end_time,
                    is_gtm=is_gtm_repair(
                        repair_type_id=repair.repair_type_id,
                        repair_type_name=type_name,
                        work_list=repair.work_list,
                    ),
                    type_name=type_name,
                    is_tech=is_technical_repair(
                        repair_type_id=repair.repair_type_id,
                        repair_type_name=type_name,
                        work_list=repair.work_list,
                    ),
                ),
            )

        month_firsts = _month_firsts(window_start, window_end)

        # Границы статусов — с запасом в сутки: интервал обрезается окном
        # уже при расчёте. 1-е число первого месяца может лежать раньше окна,
        # а фонд на него смотрит по статусу — читаем с него.
        status_rows = await self.status_repository.list_intervals_by_abai_well_ids(
            abai_ids,
            since=min(
                window_start - timedelta(days=1),
                datetime.combine(month_firsts[0], datetime.min.time()),
            ),
            until=window_end + timedelta(days=1),
        )
        work_by_well: dict[int, list[Interval]] = defaultdict(list)
        fund_status_by_well: dict[int, list[tuple[datetime, datetime]]] = defaultdict(
            list,
        )
        for abai_well_id, dbeg, dend, code, _name, _reason in status_rows:
            if code in FUND_STATUS_CODES:
                fund_status_by_well[abai_well_id].append((dbeg, dend))
            if code not in WORK_STATUS_CODES:
                continue
            end = now if dend.year > OPEN_END_YEAR else dend
            work_by_well[abai_well_id].append((dbeg, min(end, now)))

        # Действующий фонд, как считает ABAI: категория OIL и статус «в работе» /
        # «простой» / «периодическая» на 1-е число месяца.
        # Фонд на конец периода — на 1-е число месяца после окна, если оно уже
        # наступило (окно ноябрь–октябрь: фонд на 1 ноября).
        end_first = window_end.date()
        end_known = datetime.combine(end_first, datetime.min.time()) <= now
        fund_firsts = [*month_firsts, end_first] if end_known else month_firsts
        category_rows = await self.category_repository.list_intervals_by_code(
            abai_ids,
            code=FUND_CATEGORY_CODE,
            since=datetime.combine(fund_firsts[0], datetime.min.time()),
            until=datetime.combine(fund_firsts[-1], datetime.min.time()),
        )
        category_by_well: dict[int, list[tuple[datetime | None, datetime]]] = (
            defaultdict(list)
        )
        for abai_well_id, dbeg, dend in category_rows:
            category_by_well[abai_well_id].append((dbeg, dend))

        lift_by_well = await self.expl_repository.get_latest_expl_name_by_abai_well_ids(
            abai_ids,
        )

        def repair_months(abai_well_id: int) -> frozenset[tuple[int, int]]:
            return repair_fund_months_of(
                month_firsts,
                fund_months_of(
                    fund_firsts,
                    category_by_well.get(abai_well_id, []),
                    fund_status_by_well.get(abai_well_id, []),
                ),
            )

        return [
            WellFundInput(
                well_id=well.id,
                name=well.name,
                oil_field=well_name_prefix(well.name) or NO_FIELD,
                lift_type=lift_by_well.get(well.abai_id),
                work_intervals=work_by_well.get(well.abai_id, []),
                repairs=repairs_by_well.get(well.abai_id, []),
                fund_months=fund_months_of(
                    month_firsts,
                    category_by_well.get(well.abai_id, []),
                    fund_status_by_well.get(well.abai_id, []),
                ),
                repair_fund_months=repair_months(well.abai_id),
                in_fund_at_end=bool(
                    fund_months_of(
                        [end_first],
                        category_by_well.get(well.abai_id, []),
                        fund_status_by_well.get(well.abai_id, []),
                    ),
                )
                if end_known
                else None,
            )
            for well in wells
        ]
