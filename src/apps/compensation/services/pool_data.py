"""Входы пула доноров и потерь: всё, что методика берёт из данных.

Объекты — Жайкмунайгаз целиком и Кайнармунайгаз только VMB. Источники (как в
тесте 03.11.2026, docs/donor_gzu_test_criteria.md):

- состояние — таблица состояний (``wells_well_state``);
- суточная нефть — ABAI: жидкость (``emg.meas_liq``) × (1 − обводнённость
  ``emg.meas_water_cut``) × плотность из техрежима (``oil_density`` или
  нефть / (жидкость × (1 − обводнённость))); значение ABAI действует, пока не
  придёт новое;
- способ эксплуатации и глубина спуска — ревизия фонда, где пусто —
  оборудование ABAI; Ндин — более свежее из ГДИС и ревизии;
- МРП — между началами двух последних ПРС (ABAI), иначе даты ПРС из ревизии;
  наработка насоса — с последнего ТР 4-1 / 4-12;
- телеметрия — СДМО за 72 ч, только отсчёты с работающим приводом;
- ГЗУ — зеркало ГЗУ ABAI.

Время везде местное наивное, как в источниках.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean, pstdev

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.services.pool import (
    LIFT_EVN,
    LIFT_SHGN,
    SERIES_DAYS,
    PoolInput,
    Telemetry,
)
from apps.org.services import well_name_prefix
from apps.telemetry.services.drive import DRIVE_RUNNING_VLT_CODES
from apps.wells.repositories import FundRevisionRepository
from shared.constants.ngdu import KMG_ONLY_OIL_FIELD_PREFIX, AbaiNGDUIDsEnum

TELEMETRY_HOURS = 72
# Параметры глубины спуска в оборудовании ABAI (скважинные, ЭЦН, винтовые…).
ABAI_DEPTH_PARAMS = (49, 36, 6, 75, 415)
ABAI_LIFT_BY_EQUIP_TYPE = {51: LIFT_SHGN, 56: LIFT_EVN, 145: LIFT_EVN}
PRS_REPAIR_TYPE = 3  # категория ремонта ABAI: ПРС
MRP_REPAIRS = 2  # МРП — между двумя последними ПРС
FULL_WATER_CUT = 100.0

SQL_LIQUID = """
    select well, dbeg, dend, liquid from emg.meas_liq
    where well = any(:a) and dend > :s
"""
SQL_WATER_CUT = """
    select well, dbeg, dend, water_cut from emg.meas_water_cut
    where well = any(:a) and dend > :s
"""
SQL_TECH_MODE = """
    select distinct on (well) well, liquid, oil, wcut, oil_density
    from emg.tech_mode_prod_oil
    where well = any(:a) and dbeg <= :d
    order by well, dbeg desc
"""
SQL_SCOPE = """
    select w.id, w.name, w.abai_id, st.abai_ngdu_id, st.state, st.started_at
    from wells_well_state st join wells_well w on w.id = st.well_id
    where st.ended_at is null and st.state = any(:states)
      and (st.abai_ngdu_id = :zhmg or (st.abai_ngdu_id = :kmg and w.name like :vmb))
"""
SQL_GZU = """
    select w.id, t.abai_id, h.name_ru, wt.tap
    from wells_well_tech wt
    join wells_well w on w.abai_id = wt.abai_well_id
    join wells_tech t on t.abai_id = wt.abai_tech_id
    join wells_tech_type tt on tt.abai_id = t.abai_tech_type_id and tt.code = 'GMS'
    left join wells_tech_history h
      on h.abai_tech_id = t.abai_id and current_date between h.dbeg and h.dend
    where current_date between wt.dbeg and wt.dend and w.id = any(:ids)
    order by w.id, wt.dbeg desc
"""
SQL_KOEF = """
    select type_1900, addr, koef from telemetry_sdmo_fc_reg
    where addr in (1997, 1998) and koef is not null
"""
SQL_TELEMETRY = """
    select st.well_id, st.type_1900, count(*) n,
      percentile_cont(0.05) within group (order by f.r_1991) p5m,
      percentile_cont(0.95) within group (order by f.r_1991) p95m,
      avg(f.r_1991) mm, stddev_pop(f.r_1991) sm,
      avg(f.r_1991) filter (where f.savetime < :mid) m1,
      avg(f.r_1991) filter (where f.savetime >= :mid) m2,
      percentile_cont(0.05) within group (order by f.r_1997) fill5,
      percentile_cont(0.5) within group (order by f.r_1998) speed
    from telemetry_sdmo_fc_data f
    join telemetry_sdmo_station st on st.id = f.station_id
    where st.well_id = any(:ids) and f.savetime >= :since and f.savetime < :now
      and f.r_1991 between -1e9 and 1e9
      and (round(f.r_1999)::int = any(:run) or (f.r_1999 is null and f.r_1998 > 0))
    group by st.well_id, st.id, st.type_1900
"""
SQL_HDIN = """
    select distinct on (c.abai_well_id) c.abai_well_id, v.value_double, c.meas_date
    from wells_gdis_current c
    join wells_gdis_current_value v on v.gdis_current_abai_id = c.abai_id
    join wells_gdis_metric m on m.abai_id = v.metric_abai_id
    where m.name_ru = 'H дин, м' and v.value_double is not null
      and c.abai_well_id = any(:a)
    order by c.abai_well_id, c.meas_date desc
"""
SQL_PRS = """
    select abai_well_id, start_time from repairs_repair
    where abai_well_id = any(:a) and abai_deleted_at is null
      and abai_repair_type_id = :t
    order by abai_well_id, start_time desc
"""
SQL_PUMP_CHANGE = """
    select r.abai_well_id, max(r.start_time) from repairs_repair r
    join repairs_repair_type rt on rt.abai_id = r.repair_type_id
    where r.abai_well_id = any(:a) and r.abai_deleted_at is null
      and rt.name_ru ~* '^(TP|ТР) ?4-1(2)?\\M'
    group by 1
"""
SQL_DEPTH_ABAI = """
    select distinct on (we.well) we.well, wep.value_double
    from emg.well_equip we
    join emg.well_equip_param wep
      on wep.well_equip = we.id and wep.equip_param = any(:p)
    where we.dend > current_date and wep.dend > current_date
      and wep.value_double > 0 and we.well = any(:a)
    order by we.well, wep.dbeg desc
"""
SQL_LIFT_ABAI = """
    select distinct on (we.well) we.well, e.equip_type
    from emg.well_equip we join emg.equip e on e.id = we.equip
    where we.dend > current_date and e.equip_type = any(:t) and we.well = any(:a)
    order by we.well, e.equip_type
"""


def implied_density(
    liquid: float | None,
    oil: float | None,
    water_cut: float | None,
    oil_density: float | None,
) -> float | None:
    """Плотность нефти из техрежима: поле, а если пусто — нефть / (жидкость ×
    (1 − обводнённость)) (в ABAI плотность обычно не заполнена)."""
    if oil_density:
        return oil_density
    if liquid and oil and water_cut is not None and water_cut < FULL_WATER_CUT:
        return oil / (liquid * (1 - water_cut / 100))
    return None


@dataclass(frozen=True, slots=True)
class ScopeWell:
    id: int
    name: str
    abai_id: int
    abai_ngdu_id: int
    state: str
    state_since: datetime


@dataclass(frozen=True, slots=True)
class Gzu:
    abai_id: int
    name: str | None
    tap: int | None


class DailyOil:
    """Суточная нефть по ABAI на интервальных рядах (значение до следующего)."""

    def __init__(
        self,
        liquid: dict[int, list[tuple[datetime, datetime, float | None]]],
        water_cut: dict[int, list[tuple[datetime, datetime, float | None]]],
        density: dict[int, float | None],
    ) -> None:
        self.liquid = {key: sorted(rows) for key, rows in liquid.items()}
        self.water_cut = {key: sorted(rows) for key, rows in water_cut.items()}
        self.density = density

    @staticmethod
    def _value(
        rows: list[tuple[datetime, datetime, float | None]],
        day: datetime,
    ) -> float | None:
        for dbeg, dend, value in reversed(rows):
            if dbeg <= day < dend and value is not None:
                return value
        return None

    def on(self, abai_id: int, day: datetime) -> float | None:
        liquid = self._value(self.liquid.get(abai_id, []), day)
        cut = self._value(self.water_cut.get(abai_id, []), day)
        density = self.density.get(abai_id)
        if liquid is None or cut is None or density is None:
            return None
        return liquid * (1 - cut / 100) * density

    def water_cut_on(self, abai_id: int, day: datetime) -> float | None:
        return self._value(self.water_cut.get(abai_id, []), day)

    def average(self, abai_id: int, days: list[datetime]) -> float | None:
        values = [v for day in days if (v := self.on(abai_id, day)) is not None]
        return mean(values) if values else None

    def new_values(self, abai_id: int, since: datetime, until: datetime) -> int:
        return sum(
            1
            for dbeg, _dend, _v in self.liquid.get(abai_id, [])
            if since <= dbeg < until
        )


async def load_daily_oil(
    abai: AsyncSession,
    abai_ids: list[int],
    *,
    since: datetime,
    on_date: date,
) -> DailyOil:
    """Интервалы жидкости и обводнённости, пересекающие [since, …), и плотность."""
    liquid: dict[int, list] = defaultdict(list)
    water_cut: dict[int, list] = defaultdict(list)
    if abai_ids:
        for well, dbeg, dend, value in await abai.execute(
            text(SQL_LIQUID),
            {"a": abai_ids, "s": since},
        ):
            liquid[well].append((dbeg, dend, value))
        for well, dbeg, dend, value in await abai.execute(
            text(SQL_WATER_CUT),
            {"a": abai_ids, "s": since},
        ):
            water_cut[well].append((dbeg, dend, value))
    density = {}
    if abai_ids:
        rows = await abai.execute(text(SQL_TECH_MODE), {"a": abai_ids, "d": on_date})
        for well, liq, oil, wcut, oil_density in rows:
            density[well] = implied_density(liq, oil, wcut, oil_density)
    return DailyOil(liquid, water_cut, density)


async def scope_wells(app: AsyncSession, states: tuple[str, ...]) -> list[ScopeWell]:
    """Скважины объектов с открытым состоянием из ``states``."""
    rows = await app.execute(
        text(SQL_SCOPE),
        {
            "states": list(states),
            "zhmg": int(AbaiNGDUIDsEnum.ZHMG),
            "kmg": int(AbaiNGDUIDsEnum.KMG),
            "vmb": f"{KMG_ONLY_OIL_FIELD_PREFIX}\\_%",
        },
    )
    return [ScopeWell(*row) for row in rows]


async def gzu_by_well(app: AsyncSession, well_ids: list[int]) -> dict[int, Gzu]:
    """Текущая ГЗУ скважины (тип GMS) и отвод."""
    if not well_ids:
        return {}
    rows = await app.execute(text(SQL_GZU), {"ids": well_ids})
    result: dict[int, Gzu] = {}
    for well_id, abai_id, name, tap in rows:
        result.setdefault(well_id, Gzu(abai_id, name, tap))
    return result


async def _telemetry(
    app: AsyncSession,
    well_ids: list[int],
    now: datetime,
) -> dict[int, Telemetry]:
    koef = {
        (type_1900, addr): value
        for type_1900, addr, value in await app.execute(text(SQL_KOEF))
    }
    rows = await app.execute(
        text(SQL_TELEMETRY),
        {
            "ids": well_ids,
            "since": now - timedelta(hours=TELEMETRY_HOURS),
            "mid": now - timedelta(hours=TELEMETRY_HOURS / 2),
            "now": now,
            "run": sorted(DRIVE_RUNNING_VLT_CODES),
        },
    )
    result: dict[int, Telemetry] = {}
    for row in rows.mappings():
        if row["well_id"] in result and result[row["well_id"]].samples >= row["n"]:
            continue  # у скважины несколько станций — берётся самая полная
        fill_k = koef.get((row["type_1900"], 1997)) or 1.0
        speed_k = koef.get((row["type_1900"], 1998)) or 1.0
        result[row["well_id"]] = Telemetry(
            samples=row["n"],
            k=abs(row["p5m"]) / row["p95m"] if row["p95m"] else None,
            moment_cv=row["sm"] / row["mm"] if row["mm"] else None,
            moment_drift=(
                abs(row["m2"] - row["m1"]) / row["m1"]
                if row["m1"] and row["m2"] is not None
                else None
            ),
            fill_p5=row["fill5"] * fill_k if row["fill5"] is not None else None,
            speed=row["speed"] * speed_k if row["speed"] is not None else None,
        )
    return result


@dataclass(frozen=True, slots=True)
class PoolData:
    wells: list[ScopeWell]
    inputs: dict[int, PoolInput]
    gzu: dict[int, Gzu]
    # Для source_row: откуда взяты способ, глубина, Ндин; ряд и замеры.
    details: dict[int, dict]


async def load_pool_data(
    app: AsyncSession,
    abai: AsyncSession,
    now: datetime,
) -> PoolData:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window = [today - timedelta(days=SERIES_DAYS - i) for i in range(SERIES_DAYS)]
    wells = await scope_wells(app, ("working", "warning", "alarm"))
    ids = [w.id for w in wells]
    abai_ids = [w.abai_id for w in wells]

    revision = await FundRevisionRepository(app).get_latest_by_well_ids(ids)
    gzu = await gzu_by_well(app, ids)
    telemetry = await _telemetry(app, ids, now)
    hdin_gdis = {
        abai_id: (value, measured)
        for abai_id, value, measured in await app.execute(
            text(SQL_HDIN),
            {"a": abai_ids},
        )
    }
    prs: dict[int, list[datetime]] = defaultdict(list)
    for abai_id, started in await app.execute(
        text(SQL_PRS),
        {"a": abai_ids, "t": PRS_REPAIR_TYPE},
    ):
        if len(prs[abai_id]) < MRP_REPAIRS:
            prs[abai_id].append(started)
    pump_change = dict(
        (await app.execute(text(SQL_PUMP_CHANGE), {"a": abai_ids})).all(),
    )

    depth_abai = dict(
        (
            await abai.execute(
                text(SQL_DEPTH_ABAI),
                {"a": abai_ids, "p": list(ABAI_DEPTH_PARAMS)},
            )
        ).all(),
    )
    lift_abai = {}
    for abai_id, equip_type in await abai.execute(
        text(SQL_LIFT_ABAI),
        {"a": abai_ids, "t": list(ABAI_LIFT_BY_EQUIP_TYPE)},
    ):
        lift_abai[abai_id] = ABAI_LIFT_BY_EQUIP_TYPE[equip_type]
    oil = await load_daily_oil(abai, abai_ids, since=window[0], on_date=today.date())

    inputs: dict[int, PoolInput] = {}
    details: dict[int, dict] = {}
    for well in wells:
        rev = revision.get(well.id)
        lift = (rev.lift_type if rev and rev.lift_type else None) or lift_abai.get(
            well.abai_id,
        )
        lift_source = (
            "ревизия"
            if rev and rev.lift_type
            else ("ABAI" if well.abai_id in lift_abai else None)
        )
        depth = (
            rev.pump_depth_m
            if rev and rev.pump_depth_m
            else depth_abai.get(well.abai_id)
        )
        depth_source = (
            "ревизия" if rev and rev.pump_depth_m else ("ABAI" if depth else None)
        )
        candidates = []
        if well.abai_id in hdin_gdis:
            value, measured = hdin_gdis[well.abai_id]
            candidates.append((measured, value, "ГДИС"))
        if rev and rev.hdin_m is not None:
            candidates.append((rev.gdis_date or date.min, rev.hdin_m, "ревизия"))
        hdin = max(candidates, key=lambda c: c[0]) if candidates else None
        starts = prs.get(well.abai_id, [])
        if (
            len(starts) < MRP_REPAIRS
            and rev
            and rev.last_prs_date
            and rev.prev_prs_date
        ):
            starts = [
                datetime.combine(rev.last_prs_date, datetime.min.time()),
                datetime.combine(rev.prev_prs_date, datetime.min.time()),
            ]
        series = [oil.on(well.abai_id, day) for day in window]
        values = [v for v in series if v is not None]
        cuts = [
            c
            for day in window
            if (c := oil.water_cut_on(well.abai_id, day)) is not None
        ]
        average = mean(values) if values else None
        changed = pump_change.get(well.abai_id)
        inputs[well.id] = PoolInput(
            well_id=well.id,
            name=well.name,
            field=well_name_prefix(well.name) or "",
            state=well.state,
            lift=lift,
            days=len(values),
            oil=average,
            cv=pstdev(values) / average if len(values) > 1 and average else None,
            flat=bool(values) and len({round(v, 4) for v in values}) == 1,
            water_cut=mean(cuts) if cuts else None,
            pump_depth=depth,
            hdin=hdin[1] if hdin else None,
            mrp_days=(
                (starts[0] - starts[1]).days if len(starts) >= MRP_REPAIRS else None
            ),
            since_repair_days=(now - starts[0]).days if starts else None,
            pump_age_days=(now - changed).days if changed else None,
            telemetry=telemetry.get(well.id),
        )
        details[well.id] = {
            "lift_source": lift_source,
            "depth_source": depth_source,
            "hdin_source": hdin[2] if hdin else None,
            "hdin_date": hdin[0].isoformat() if hdin and hdin[0] != date.min else None,
            "revision_date": rev.revision_date.isoformat() if rev else None,
            "new_values": oil.new_values(well.abai_id, window[0], today),
            "last_oil": series[-1],
        }
    return PoolData(wells=wells, inputs=inputs, gzu=gzu, details=details)
