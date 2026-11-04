"""Потери контура: какие скважины стоят и сколько нефти теряется.

Стоит — открытое состояние «стоит» или «ремонт» в таблице состояний скважин
(ABAI + привод СДМО: привод стоит дольше порога — тоже остановка, даже если
ABAI пишет «В работе»). Величина — средняя фактическая нефть по ABAI за 10
суток до остановки (методика, п. 8.1: режимный дебит вместо фактического не
допускается). Факта нет — план техрежима с пометкой источника; нет и плана —
величина пуста (потерю не оценить).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.constants import (
    LOSS_RESOLVED_AFTER_HOURS,
    PLAN_GRACE_DAYS,
    STOP_IDLE,
    STOP_REPAIR,
)
from apps.compensation.services.pool_data import (
    SQL_TECH_MODE,
    DailyOil,
    gzu_by_well,
    implied_density,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from core import get_logger

logger = get_logger(__name__)

LOSS_DAYS = 10
MIN_FACT = 0.1  # т/сут: меньше — считаем, что факта нет
SOURCE_FACT = "fact"
SOURCE_PLAN = "plan"
STOP_KIND_BY_STATE = {"stopped": STOP_IDLE, "repair": STOP_REPAIR}
RUNNING_STATES = frozenset({"working", "warning", "alarm"})
WRITTEN_OFF_STATES = frozenset({"inactive"})

# Интервалы ABAI только в окнах скважин: у давних остановок окно в прошлом.
SQL_WINDOW = """
    select m.well, m.dbeg, m.dend, m.{column} from emg.{table} m
    join unnest(
      cast(:w as bigint[]), cast(:s as timestamp[]), cast(:u as timestamp[])
    ) as x(well, since, until)
      on m.well = x.well and m.dend > x.since and m.dbeg < x.until
    -- Условия на саму таблицу уходят в удалённый запрос FDW: без них
    -- планировщик мог бы вытянуть весь meas_liq.
    where m.well = any(cast(:w as bigint[])) and m.dend > :min_since
"""
SQL_STOPPED = """
    select well_id, state, started_at, reason_ru from wells_well_state
    where ended_at is null and state in ('stopped', 'repair')
      and well_id = any(:ids)
"""
SQL_CURRENT_STATE = """
    select well_id, state, started_at from wells_well_state
    where ended_at is null and well_id = any(:ids)
"""


@dataclass(frozen=True, slots=True)
class LossValue:
    well_id: int
    stop_kind: str
    stop_text: str | None
    stop_since: datetime
    loss: float | None
    source: str | None
    gzu_abai_id: int | None


def resolution_pending(
    state: tuple[str, datetime] | None,
    *,
    now: datetime,
) -> bool:
    """Потеря ушла из «стоит», но снятой её считать рано: состояние неизвестно
    (нет связи, нет строки) или пуск свежее ``LOSS_RESOLVED_AFTER_HOURS``."""
    if state is None:
        return True
    kind, since = state
    if kind in WRITTEN_OFF_STATES:
        return False
    if kind in RUNNING_STATES:
        return since > now - timedelta(hours=LOSS_RESOLVED_AFTER_HOURS)
    return True


async def pending_resolution(
    app: AsyncSession,
    well_ids: list[int],
    *,
    now: datetime,
) -> set[int]:
    """Из скважин, ушедших из «стоит», — те, чьи пары пока держать."""
    if not well_ids:
        return set()
    rows = await app.execute(text(SQL_CURRENT_STATE), {"ids": well_ids})
    states = {well_id: (state, since) for well_id, state, since in rows}
    return {
        well_id
        for well_id in well_ids
        if resolution_pending(states.get(well_id), now=now)
    }


def loss_window(since: datetime) -> list[datetime]:
    """Сутки, по которым считается факт: LOSS_DAYS полных суток до остановки."""
    start = since.replace(hour=0, minute=0, second=0, microsecond=0)
    return [start - timedelta(days=i) for i in range(1, LOSS_DAYS + 1)]


def loss_value(
    fact: float | None,
    plan: float | None,
) -> tuple[float | None, str | None]:
    if fact is not None and fact >= MIN_FACT:
        return fact, SOURCE_FACT
    if plan is not None and plan > 0:
        return plan, SOURCE_PLAN
    return None, None


async def _daily_oil_windows(
    abai: AsyncSession,
    windows: dict[int, tuple[datetime, datetime]],
    *,
    on_date: datetime,
) -> DailyOil:
    """Интервалы ABAI только в окнах скважин (у давних остановок окно в прошлом)."""
    liquid: dict[int, list] = defaultdict(list)
    water_cut: dict[int, list] = defaultdict(list)
    density: dict[int, float | None] = {}
    if not windows:
        return DailyOil(liquid, water_cut, density)
    wells = list(windows)
    params = {
        "w": wells,
        "s": [windows[w][0] for w in wells],
        "u": [windows[w][1] for w in wells],
        "min_since": min(since for since, _until in windows.values()),
    }
    for table, column, target in (
        ("meas_liq", "liquid", liquid),
        ("meas_water_cut", "water_cut", water_cut),
    ):
        # Имена таблиц и колонок — константы выше, не ввод пользователя.
        query = SQL_WINDOW.format(table=table, column=column)
        rows = await abai.execute(text(query), params)
        for well, dbeg, dend, value in rows:
            target[well].append((dbeg, dend, value))
    rows = await abai.execute(
        text(SQL_TECH_MODE),
        {"a": wells, "d": on_date.date()},
    )
    for well, liq, oil, wcut, oil_density in rows:
        density[well] = implied_density(liq, oil, wcut, oil_density)
    return DailyOil(liquid, water_cut, density)


async def compute_losses(
    app: AsyncSession,
    abai: AsyncSession,
    wells: list,
    *,
    now: datetime,
) -> list[LossValue]:
    """``wells`` — скважины контура (``Well``); ``now`` — местное наивное время."""
    by_id = {well.id: well for well in wells}
    if not by_id:
        return []
    states = (await app.execute(text(SQL_STOPPED), {"ids": list(by_id)})).all()
    if not states:
        return []
    windows = {}
    for well_id, _state, since, _reason in states:
        days = loss_window(since)
        windows[by_id[well_id].abai_id] = (days[-1], days[0] + timedelta(days=1))
    try:
        oil = await _daily_oil_windows(abai, windows, on_date=now)
    except Exception:
        # ABAI недоступен — потери по плану: закрытия пар не должны вставать.
        logger.exception("ABAI daily oil unavailable, losses fall back to plan")
        await abai.rollback()
        oil = DailyOil({}, {}, {})
    regimes = await TechRegimeRepository(app).get_current_by_abai_well_ids(
        [by_id[well_id].abai_id for well_id, *_ in states],
        on_date=now.date(),
        grace_days=PLAN_GRACE_DAYS,
    )
    gzu = await gzu_by_well(app, [well_id for well_id, *_ in states])
    result = []
    for well_id, state, since, reason in states:
        well = by_id[well_id]
        facts = [
            v
            for day in loss_window(since)
            if (v := oil.on(well.abai_id, day)) is not None
        ]
        regime = regimes.get(well.abai_id)
        value, source = loss_value(
            mean(facts) if facts else None,
            regime.oil if regime is not None else None,
        )
        result.append(
            LossValue(
                well_id=well_id,
                stop_kind=STOP_KIND_BY_STATE[state],
                stop_text=reason,
                stop_since=since,
                loss=round(value, 3) if value is not None else None,
                source=source,
                gzu_abai_id=gzu[well_id].abai_id if well_id in gzu else None,
            ),
        )
    return result
