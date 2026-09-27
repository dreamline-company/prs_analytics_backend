"""Общий отбор ремонтов-кандидатов для добытчиков данных и аналитики.

Кандидат — ремонт, по которому ещё есть смысл тянуть данные из источников и
пересчитывать аналитику: не финализирован и при этом либо активен, либо
закончился не раньше ``GRACE_DAYS`` назад, либо вовсе без записи аналитики.
Точечные прогоны по ``repair_id`` / ``well_id`` / ``repair_ids`` фильтр
финализации игнорируют — это ручной перезапуск или явный триггер.

Один модуль на всех потребителей: добытчики ABAI, УТО, СПО и аналитика должны
смотреть на один и тот же набор ремонтов, иначе данные тянутся для одних, а
аналитика считается по другим.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.repairs.models.analytics import (
    AI_STATUS_FAILED,
    RepairAIAnalysis,
    RepairAnalytics,
)
from apps.repairs.models.repair import Repair
from apps.wells.models.well import Well
from apps.wells.repositories.well import WellRepository
from core.settings import get_settings

settings = get_settings()

GRACE_DAYS = 10
# Ремонт с упавшим общим вердиктом остаётся кандидатом аналитики ещё столько
# дней после GRACE_DAYS — вердикт повторяется каждым проходом, пока не удастся.
AI_RETRY_DAYS = 30
ITER_SIZE = 200
# Замер КБРС считается закрытым, когда опросчик перестал его перечитывать:
# правый край старше окна обновления живых замеров.
LIVE_MEASURE_GRACE = timedelta(minutes=settings.KBRS_POLL_REFRESH_GRACE_MINUTES)
# Окно поиска СПО: замер может закончиться на следующие сутки после даты
# окончания ремонта (даты ABAI — без времени).
SPO_WINDOW_TAIL = timedelta(days=1)


@dataclass(slots=True)
class RunStats:
    """Итог прогона добытчика: сколько ремонтов обработано, изменено, упало."""

    processed: int = 0
    changed: int = 0
    failed: int = 0


def local_now() -> datetime:
    return datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)


def as_naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def grace_cutoff(now: datetime) -> datetime:
    return now - timedelta(days=GRACE_DAYS)


def ai_retry_cutoff(now: datetime) -> datetime:
    """Раньше этой даты закрытый ремонт с упавшим вердиктом уже не повторяем."""
    return now - timedelta(days=GRACE_DAYS + AI_RETRY_DAYS)


def repair_window(repair: Repair, *, now: datetime) -> tuple[datetime, datetime]:
    """Интервал, в котором ищутся замеры СПО ремонта: [start, end + сутки]."""
    start = as_naive(repair.start_time)
    end = as_naive(repair.end_time) if repair.end_time is not None else now
    return start, end + SPO_WINDOW_TAIL


def is_measure_closed(
    end_time: datetime | None,
    *,
    now: datetime,
    grace: timedelta = LIVE_MEASURE_GRACE,
) -> bool:
    """Замер закрыт — прибор перестал писать дольше, чем окно опросчика."""
    if end_time is None:
        return False
    return now - as_naive(end_time) > grace


async def iter_candidate_repairs(  # noqa: PLR0913
    session: AsyncSession,
    *,
    grace_cutoff: datetime,
    repair_id: int | None = None,
    well_id: int | None = None,
    repair_ids: Sequence[int] | None = None,
    batch_size: int = ITER_SIZE,
    retry_failed_ai_since: datetime | None = None,
) -> AsyncIterator[Sequence[Repair]]:
    """Ремонты-кандидаты батчами по возрастанию id.

    Явная область (``repair_id``, ``well_id``, ``repair_ids``) отдаёт ремонты
    без фильтра финализации. Без области — только кандидаты по правилу модуля;
    с ``retry_failed_ai_since`` — ещё и незафинализированные ремонты с упавшим
    общим вердиктом, закрытые не раньше этой даты (только для аналитики).
    """
    if repair_id is not None:
        repairs = (
            (await session.execute(select(Repair).where(Repair.id == repair_id)))
            .scalars()
            .all()
        )
        if repairs:
            yield repairs
        return

    last_id = 0
    while True:
        qs = (
            select(Repair)
            .where(Repair.id > last_id)
            .order_by(Repair.id.asc())
            .limit(batch_size)
        )
        if repair_ids is not None:
            qs = qs.where(Repair.id.in_(list(repair_ids)))
        elif well_id is not None:
            qs = qs.join(Well, Repair.abai_well_id == Well.abai_id).where(
                Well.id == well_id,
            )
        else:
            window = [
                Repair.end_time.is_(None),
                Repair.end_time >= grace_cutoff,
                RepairAnalytics.id.is_(None),
            ]
            if retry_failed_ai_since is not None:
                failed_ai = select(RepairAIAnalysis.analytics_id).where(
                    RepairAIAnalysis.status == AI_STATUS_FAILED,
                )
                window.append(
                    and_(
                        Repair.end_time >= retry_failed_ai_since,
                        RepairAnalytics.id.in_(failed_ai),
                    ),
                )
            qs = qs.outerjoin(
                RepairAnalytics,
                RepairAnalytics.repair_id == Repair.id,
            ).where(
                or_(
                    RepairAnalytics.is_finalized.is_(None),
                    RepairAnalytics.is_finalized.is_(False),
                ),
                or_(*window),
            )
        repairs = (await session.execute(qs)).scalars().all()
        if not repairs:
            return
        yield repairs
        last_id = repairs[-1].id
        if len(repairs) < batch_size:
            return


async def list_candidate_repairs(
    session: AsyncSession,
    *,
    grace_cutoff: datetime,
    repair_id: int | None = None,
    well_id: int | None = None,
    repair_ids: Sequence[int] | None = None,
) -> list[Repair]:
    result: list[Repair] = []
    async for batch in iter_candidate_repairs(
        session,
        grace_cutoff=grace_cutoff,
        repair_id=repair_id,
        well_id=well_id,
        repair_ids=repair_ids,
    ):
        result.extend(batch)
    return result


async def resolve_repair_well(
    repair: Repair,
    well_repo: WellRepository,
) -> Well | None:
    """Скважина ремонта: ``Repair.well_id`` загрузчик не заполняет, реальная
    связь живёт в ``abai_well_id``."""
    if repair.well_id is not None:
        well = await well_repo.get_by_id(id_=repair.well_id)
        if well is not None:
            return well
    if repair.abai_well_id is not None:
        return await well_repo.get_by_abai_id(abai_id=repair.abai_well_id)
    return None


def scope_label(
    *,
    repair_id: int | None,
    well_id: int | None,
    repair_ids: Sequence[int] | None = None,
) -> str:
    if repair_id is not None:
        return f"repair_id={repair_id}"
    if well_id is not None:
        return f"well_id={well_id}"
    if repair_ids is not None:
        return f"repair_ids={len(repair_ids)}"
    return "all candidates"
