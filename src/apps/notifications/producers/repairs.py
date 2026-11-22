"""Начало ремонта (``well.repair_started``).

Цепочка — ремонт: источник ``SOURCE_REPAIR`` и id ``repairs_repair``. Шлётся,
когда у нас появился новый ремонт из ABAI (``repairs.sync`` каждые 30 минут):
шаг keyset и дочитка пропавших. ``event_at`` — начало ремонта: ремонт,
внесённый в ABAI позже чем через сутки после начала, эмиттер отбросит — это
намеренно (решение 23.11.2026).

Модуль только строит DTO; пишет их ``LoadRepairs`` перед ``commit``.
"""

from collections.abc import Iterable
from datetime import datetime

from apps.notifications.constants import MAX_EVENT_AGE, SEVERITY_INFO
from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationFactDTO,
)
from apps.notifications.producers.common import (
    SOURCE_REPAIR,
    dedup_key,
    fmt_dt,
    well_title,
)
from apps.repairs.dto.internal.repair import StartedRepairDTO

EVENT_REPAIR_STARTED = "well.repair_started"

# Тип ремонта ABAI (well_workover.repair_type -> well_repair_type), см.
# ``Repair.abai_repair_type_id``. На проде есть только КРС и ПРС.
REPAIR_CATEGORIES_RU: dict[int, str] = {
    1: "КРС",
    2: "ТРС",
    3: "ПРС",
    4: "прочие простои",
    5: "наземный ремонт",
}

_TITLE_MAX = 255


def repair_category_ru(abai_repair_type_id: int | None) -> str | None:
    if abai_repair_type_id is None:
        return None
    return REPAIR_CATEGORIES_RU.get(abai_repair_type_id)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def recent_starts(
    starts: Iterable[tuple[int, datetime | None]],
    now: datetime,
) -> list[int]:
    """abai_id ремонтов, начатых за последние сутки и не позже ``now``.

    Старые эмиттер всё равно отбросит, а без отсева первая загрузка
    перечитывала бы все ремонты. Ремонт с началом в будущем ещё не начался —
    «начался ремонт» о нём не шлётся (и позже тоже: повторной проверки нет).
    """
    since = now - MAX_EVENT_AGE
    return [
        abai_id
        for abai_id, start in starts
        if isinstance(start, datetime) and since <= start <= now
    ]


def repair_started_notification(repair: StartedRepairDTO) -> NotificationCreateDTO:
    category = repair_category_ru(repair.abai_repair_type_id)
    kind = repair.repair_type_name_ru
    details = ", ".join(part for part in (category, kind) if part)
    text = f"начался ремонт ({details})" if details else "начался ремонт"
    facts = [NotificationFactDTO(label_ru="Начало", value_ru=fmt_dt(repair.start_time))]
    if kind:
        facts.append(NotificationFactDTO(label_ru="Вид работ", value_ru=kind))
    if category:
        facts.append(NotificationFactDTO(label_ru="Категория", value_ru=category))
    return NotificationCreateDTO(
        event_code=EVENT_REPAIR_STARTED,
        severity=SEVERITY_INFO,
        title_ru=_clip(well_title(repair.well_name, text), _TITLE_MAX),
        well_id=repair.well_id,
        source_type=SOURCE_REPAIR,
        source_id=repair.id,
        dedup_key=dedup_key(EVENT_REPAIR_STARTED, SOURCE_REPAIR, repair.id),
        payload={
            "repair_id": repair.id,
            "abai_repair_id": repair.abai_id,
            "abai_repair_type_id": repair.abai_repair_type_id,
        },
        facts=facts,
        event_at=repair.start_time,
    )
