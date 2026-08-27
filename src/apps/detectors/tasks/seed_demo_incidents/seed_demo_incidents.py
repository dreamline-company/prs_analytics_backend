"""Демо-данные подсистемы детекции: эпизоды R2 и R9 на реальных скважинах.

Нужны, пока боевые раннеры не по чему гонять (телеметрия ШГН в app-копию не
залита), а API инцидентов и фронт уже надо смотреть.

Всё, что пишет этот скрипт, помечено суффиксом ``-demo`` в ``config_version``
— по нему же и удаляется, поэтому боевые эпизоды снести нельзя:

    python -m apps.detectors.tasks.seed_demo_incidents.seed_demo_incidents
    python -m apps.detectors.tasks.seed_demo_incidents.seed_demo_incidents --reset
    python -m apps.detectors.tasks.seed_demo_incidents.seed_demo_incidents --only-reset

Набор покрывает все состояния, которые различает UI: активный аларм,
активный warning, закрытый по восстановлению и закрытый как stale — по обоим
правилам. Скважины берутся реальные (иначе FK на ``wells_well``), станции —
из целевых флотов правил: R9 — ШГН (``type_1900 = 1``), R2 — ``type_1900 = 6``.
"""

import argparse
import asyncio
import random
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.load_imbalance import config as r9_config
from apps.detectors.load_imbalance import incident_config as r9_incident_config
from apps.detectors.models.incident import (
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_ACTIVE,
    INCIDENT_STATUS_NORMALIZED,
    DetectorIncident,
)
from apps.detectors.rod_breaks import config as r2_config
from apps.detectors.rod_breaks import incident_config as r2_incident_config
from apps.detectors.rod_breaks.tasks.run_incidents.run_incidents import (
    TARGET_TYPE_1900 as R2_TYPE_1900,
)
from apps.models_registry import *  # noqa: F403
from apps.telemetry.models.sdmo import SdmoStation
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

# Метка демо-данных: этим суффиксом отличаются насеянные эпизоды от боевых.
DEMO_SUFFIX = "-demo"
R9_CONFIG_VERSION = f"{r9_config.CONFIG_VERSION}{DEMO_SUFFIX}"
R2_CONFIG_VERSION = f"{r2_config.CONFIG_VERSION}{DEMO_SUFFIX}"

DEFAULT_SEED = 20260826
# Сколько скважин занять под каждое правило.
DEFAULT_WELLS_PER_DETECTOR = 8


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min)


# --- R9: перекос нагрузки ---------------------------------------------------


def _r9_day(
    rnd: random.Random,
    day: date,
    *,
    sick: bool,
    healthy_p95: float,
) -> dict:
    """Сутки-улика R9: здоровые или с ушедшей полезной нагрузкой.

    Больные сутки ломают баланс с двух сторон разом — рабочий пик падает,
    обратный растёт, — поэтому K и берётся отношением: он чувствителен вдвойне.
    """
    if sick:
        p95 = healthy_p95 * rnd.uniform(0.45, 0.65)
        p5 = -p95 * rnd.uniform(0.28, 0.52)
    else:
        p95 = healthy_p95 * rnd.uniform(0.94, 1.06)
        p5 = -healthy_p95 * rnd.uniform(0.015, 0.055)

    k = abs(p5) / p95
    if k >= r9_config.K_ALERT:
        state = "alert"
    elif k >= r9_config.K_DEGRADE:
        state = "grey"
    else:
        state = "clean"

    return {
        "day": day.isoformat(),
        "state": state,
        "n": rnd.randint(230, 288),
        "k": round(k, 4),
        "p5": round(p5, 1),
        "p50": round(p95 * rnd.uniform(0.22, 0.34), 1),
        "p95": round(p95, 1),
    }


def _r9_payload(
    rnd: random.Random,
    *,
    opened_day: date,
    until_day: date,
    healthy_p95: float,
) -> dict:
    days = [
        until_day - timedelta(days=offset)
        for offset in reversed(range(r9_incident_config.EVIDENCE_DAYS))
    ]
    evidence = [
        _r9_day(rnd, day, sick=day >= opened_day, healthy_p95=healthy_p95)
        for day in days
    ]
    last = evidence[-1]
    k_p90 = round(rnd.uniform(0.035, 0.075), 4)

    branches = ["absolute"] if last["k"] >= r9_config.K_ALERT else []
    if last["k"] >= r9_config.K_DEGRADE and last["k"] >= 1.5 * k_p90:
        branches.append("relative")

    return {
        "k": last["k"],
        "branches": branches,
        "k_alert": r9_config.K_ALERT,
        "k_degrade": r9_config.K_DEGRADE,
        "baseline": {
            "n_days": rnd.randint(38, 60),
            "k_p90": k_p90,
            "p95_median": round(healthy_p95, 1),
            "abs_branch_muted": False,
        },
        "evidence": evidence,
    }


def _r9_incident(  # noqa: PLR0913
    rnd: random.Random,
    *,
    station: SdmoStation,
    opened_day: date,
    alert_days: int,
    status: str,
    close_reason: str | None,
) -> DetectorIncident:
    healthy_p95 = rnd.uniform(2900.0, 4200.0)
    # Правая граница последних подтверждённых суток — полночь следующих.
    last_day = opened_day + timedelta(days=alert_days - 1)
    last_seen_at = _midnight(last_day + timedelta(days=1))
    is_alarm = alert_days >= r9_incident_config.ALARM_ALERT_DAYS

    normalized_at = None
    if status == INCIDENT_STATUS_NORMALIZED:
        gap = (
            r9_incident_config.RECOVER_SUSTAIN_DAYS
            if close_reason == CLOSE_REASON_RECOVERED
            else r9_incident_config.STALE_DAYS + 1
        )
        normalized_at = last_seen_at + timedelta(days=gap)

    return DetectorIncident(
        detector_code="R9",
        well_id=station.well_id,
        entity_id=station.sdmo_id,
        reason_code=r9_incident_config.REASON_LOAD_IMBALANCE,
        level=INCIDENT_LEVEL_ALARM if is_alarm else INCIDENT_LEVEL_WARNING,
        status=status,
        opened_at=_midnight(opened_day),
        # Правило суточное и бежит в 04:10 следующих суток — отсюда лаг детекции.
        detected_at=_midnight(opened_day + timedelta(days=1))
        + timedelta(hours=4, minutes=10),
        last_seen_at=last_seen_at,
        escalated_at=_midnight(opened_day + timedelta(days=2)) if is_alarm else None,
        normalized_at=normalized_at,
        close_reason=close_reason,
        config_version=R9_CONFIG_VERSION,
        payload=_r9_payload(
            rnd,
            opened_day=opened_day,
            until_day=last_day,
            healthy_p95=healthy_p95,
        ),
    )


# --- R2: обрыв штанги -------------------------------------------------------


def _r2_payload(
    rnd: random.Random,
    *,
    opened_at: datetime,
    last_seen_at: datetime,
    base_moment: float,
    with_failure: bool,
) -> dict:
    step = timedelta(hours=2)
    buckets = [
        last_seen_at - step * offset
        for offset in reversed(range(r2_incident_config.EVIDENCE_BUCKETS))
    ]
    evidence = []
    for start_ts in buckets:
        broken = start_ts >= opened_at
        ratio = rnd.uniform(0.25, 0.38) if broken else rnd.uniform(0.82, 0.98)
        evidence.append(
            {
                "bucket": start_ts.isoformat(),
                "mom_min": round(base_moment * ratio, 2),
                # Скорость живая: привод крутится, нагрузки на нём нет.
                "spd": round(rnd.uniform(44.0, 50.0), 2),
                "mom_med24": round(base_moment * rnd.uniform(0.97, 1.03), 2),
            },
        )

    last = evidence[-1]
    payload: dict = {
        "base_moment": round(base_moment, 2),
        "warn_threshold": r2_incident_config.WARN_MOM_DROP_RATIO,
        "alarm_threshold": r2_config.MOM_DROP_RATIO,
        "current_ratio": round(last["mom_min"] / last["mom_med24"], 3),
        "evidence": evidence,
    }
    if with_failure:
        payload["failure_dt"] = opened_at.isoformat()
        payload["event_class"] = "confirmed"
        payload["lead_time_hours"] = 0.0
        payload["low_confidence"] = base_moment < r2_config.MIN_BASE_MOMENT
    return payload


def _r2_incident(  # noqa: PLR0913
    rnd: random.Random,
    *,
    station: SdmoStation,
    opened_at: datetime,
    duration_hours: int,
    status: str,
    level: str,
) -> DetectorIncident:
    base_moment = rnd.uniform(32.0, 58.0)
    last_seen_at = opened_at + timedelta(hours=duration_hours)
    is_alarm = level == INCIDENT_LEVEL_ALARM

    normalized_at = None
    if status == INCIDENT_STATUS_NORMALIZED:
        # Гистерезис закрытия: RECOVER_SUSTAIN_BUCKETS корзин по 15 минут.
        normalized_at = last_seen_at + timedelta(
            minutes=15 * r2_incident_config.RECOVER_SUSTAIN_BUCKETS,
        )

    return DetectorIncident(
        detector_code="R2",
        well_id=station.well_id,
        entity_id=station.sdmo_id,
        reason_code=r2_incident_config.REASON_ROD_BREAK,
        level=level,
        status=status,
        opened_at=opened_at,
        # R2 бежит на каждый инкремент телеметрии — лаг в пределах корзины.
        detected_at=opened_at + timedelta(minutes=rnd.randint(20, 75)),
        last_seen_at=last_seen_at,
        escalated_at=opened_at + timedelta(hours=2) if is_alarm else None,
        normalized_at=normalized_at,
        close_reason=CLOSE_REASON_RECOVERED if normalized_at else None,
        config_version=R2_CONFIG_VERSION,
        payload=_r2_payload(
            rnd,
            opened_at=opened_at,
            last_seen_at=last_seen_at,
            base_moment=base_moment,
            with_failure=is_alarm,
        ),
    )


# --- Сборка -----------------------------------------------------------------


async def _pick_stations(
    session: AsyncSession,
    *,
    type_1900: int,
    limit: int,
) -> list[SdmoStation]:
    """Станции целевого флота: одна на скважину, с привязкой к ней."""
    rows = await session.execute(
        select(SdmoStation)
        .where(
            SdmoStation.type_1900 == type_1900,
            SdmoStation.well_id.is_not(None),
        )
        .order_by(SdmoStation.sdmo_id),
    )
    by_well: dict[int, SdmoStation] = {}
    for station in rows.scalars():
        by_well.setdefault(station.well_id, station)
    return sorted(by_well.values(), key=lambda s: s.sdmo_id)[:limit]


# Профили эпизодов R9: (возраст в сутках, alert-суток, статус, причина закрытия).
# Чередуются по скважинам, чтобы в выгрузке нашлись все четыре состояния —
# активный аларм, активный warning, закрытый по восстановлению и stale.
R9_PROFILES = (
    ((3, 9), (2, 5), INCIDENT_STATUS_ACTIVE, None),
    ((2, 2), (1, 1), INCIDENT_STATUS_ACTIVE, None),
    ((40, 70), (2, 6), INCIDENT_STATUS_NORMALIZED, CLOSE_REASON_RECOVERED),
    ((75, 110), (1, 3), INCIDENT_STATUS_NORMALIZED, CLOSE_REASON_STALE),
)

# Профили эпизодов R2: (часов назад открыт, длительность в часах, статус,
# уровень). Правило корзинное, поэтому единица здесь — часы, а не сутки.
R2_PROFILES = (
    ((6, 30), (6, 26), INCIDENT_STATUS_ACTIVE, INCIDENT_LEVEL_ALARM),
    ((2, 8), (2, 6), INCIDENT_STATUS_ACTIVE, INCIDENT_LEVEL_WARNING),
    ((480, 1440), (8, 40), INCIDENT_STATUS_NORMALIZED, INCIDENT_LEVEL_ALARM),
)

# Каждой второй скважине — ещё и старый закрытый эпизод: карточка должна
# показывать историю, а не только текущее состояние.
HISTORY_EVERY = 2

# Индекс ШГН-скважины, которой доложат второй активный сигнал: у неё по
# профилю висит warning от R9, поверх ляжет аларм R2.
OVERLAP_STATION_INDEX = 1


def _build_r9(
    rnd: random.Random,
    stations: list[SdmoStation],
    today: date,
) -> list[DetectorIncident]:
    """По одному активному эпизоду на скважину плюс закрытая история.

    Активный ровно один: частичный уникальный индекс не даёт держать два
    активных эпизода на тройку (правило, скважина, сигнал).
    """
    incidents: list[DetectorIncident] = []
    for index, station in enumerate(stations):
        age, alert, status, close_reason = R9_PROFILES[index % len(R9_PROFILES)]
        incidents.append(
            _r9_incident(
                rnd,
                station=station,
                opened_day=today - timedelta(days=rnd.randint(*age)),
                alert_days=rnd.randint(*alert),
                status=status,
                close_reason=close_reason,
            ),
        )
        if index % HISTORY_EVERY == 0:
            incidents.append(
                _r9_incident(
                    rnd,
                    station=station,
                    opened_day=today - timedelta(days=rnd.randint(120, 200)),
                    alert_days=rnd.randint(2, 4),
                    status=INCIDENT_STATUS_NORMALIZED,
                    close_reason=CLOSE_REASON_RECOVERED,
                ),
            )
    return incidents


def _build_r2(
    rnd: random.Random,
    stations: list[SdmoStation],
    now: datetime,
) -> list[DetectorIncident]:
    incidents: list[DetectorIncident] = []
    for index, station in enumerate(stations):
        age, duration, status, level = R2_PROFILES[index % len(R2_PROFILES)]
        opened_ago = rnd.randint(*age)
        incidents.append(
            _r2_incident(
                rnd,
                station=station,
                opened_at=now - timedelta(hours=opened_ago),
                # Эпизод не может тянуться дольше собственного возраста —
                # иначе last_seen_at уезжает в будущее.
                duration_hours=min(rnd.randint(*duration), opened_ago),
                status=status,
                level=level,
            ),
        )
    return incidents


def _build_overlap(
    rnd: random.Random,
    stations: list[SdmoStation],
    now: datetime,
) -> list[DetectorIncident]:
    """Скважина, на которой висят два активных сигнала разом.

    Уникальный индекс это разрешает — он на тройку (правило, скважина, сигнал),
    — и карточка должна свернуть такое в худший уровень, показав оба. Берётся
    скважина с активным warning от R9, чтобы аларм R2 лёг поверх него.
    """
    if len(stations) < OVERLAP_STATION_INDEX + 1:
        return []

    station = stations[OVERLAP_STATION_INDEX]
    opened_ago = rnd.randint(6, 30)
    return [
        _r2_incident(
            rnd,
            station=station,
            opened_at=now - timedelta(hours=opened_ago),
            duration_hours=min(rnd.randint(6, 26), opened_ago),
            status=INCIDENT_STATUS_ACTIVE,
            level=INCIDENT_LEVEL_ALARM,
        ),
    ]


async def reset_demo(session: AsyncSession) -> int:
    """Снести ранее насеянное. Боевые эпизоды не трогает — фильтр по метке."""
    result = await session.execute(
        delete(DetectorIncident).where(
            DetectorIncident.config_version.in_(
                [R9_CONFIG_VERSION, R2_CONFIG_VERSION],
            ),
        ),
    )
    return result.rowcount or 0


async def seed(
    session: AsyncSession,
    *,
    wells_per_detector: int,
    seed_value: int,
) -> list[DetectorIncident]:
    rnd = random.Random(seed_value)  # noqa: S311 — демо-данные, не криптография
    # Наивный UTC — как во всех колонках detectors_* (DateTime без таймзоны).
    now = datetime.now(UTC).replace(tzinfo=None)

    r9_stations = await _pick_stations(
        session,
        type_1900=r9_config.TARGET_TYPE_1900,
        limit=wells_per_detector,
    )
    r2_stations = await _pick_stations(
        session,
        type_1900=R2_TYPE_1900,
        limit=wells_per_detector,
    )
    if not r9_stations and not r2_stations:
        msg = "Нет станций с привязкой к скважине — сеять не на что"
        raise RuntimeError(msg)

    incidents = (
        _build_r9(rnd, r9_stations, now.date())
        + _build_r2(rnd, r2_stations, now)
        + _build_overlap(rnd, r9_stations, now)
    )
    session.add_all(incidents)
    return incidents


async def main(
    *,
    wells_per_detector: int = DEFAULT_WELLS_PER_DETECTOR,
    seed_value: int = DEFAULT_SEED,
    reset: bool = False,
    only_reset: bool = False,
) -> None:
    async with session_makers["app"]() as session:
        if reset or only_reset:
            removed = await reset_demo(session)
            logger.info("Demo incidents removed: %s", removed)
        if not only_reset:
            incidents = await seed(
                session,
                wells_per_detector=wells_per_detector,
                seed_value=seed_value,
            )
            wells = {incident.well_id for incident in incidents}
            logger.info(
                "Demo incidents seeded: %s on %s wells",
                len(incidents),
                len(wells),
            )
        await session.commit()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wells",
        type=int,
        default=DEFAULT_WELLS_PER_DETECTOR,
        help="скважин на каждое правило",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="сначала удалить ранее насеянные демо-эпизоды",
    )
    parser.add_argument(
        "--only-reset",
        action="store_true",
        help="только удалить, ничего не сеять",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        main(
            wells_per_detector=args.wells,
            seed_value=args.seed,
            reset=args.reset,
            only_reset=args.only_reset,
        ),
    )
