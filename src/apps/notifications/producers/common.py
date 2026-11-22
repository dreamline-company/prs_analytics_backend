"""Общее для производителей: ключи, подписи, запись пачкой."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.dto.internal.notification import NotificationCreateDTO
from apps.notifications.services.emitter import notification_emitter
from shared.constants.ngdu import AbaiNGDUIDsEnum

# Типы источников: цепочка в карточке = уведомления с одним источником.
SOURCE_INCIDENT = "detectors_incident"  # эпизод детектора, id эпизода
SOURCE_WELL_STOP = "well_stop"  # остановка привода, id строки wells_well_state
SOURCE_REPAIR = "repair"  # ремонт, id repairs_repair
SOURCE_FEED_SDMO = "feed_sdmo"  # сбой СДМО по НГДУ, см. feed_source_id
SOURCE_FEED_CITS = "feed_cits"  # нет замеров ЦИТС по НГДУ, см. feed_source_id
SOURCE_FEED_ABAI = "feed_abai"  # защита от пустого ответа ABAI

NGDU_NAMES_RU: dict[int, str] = {
    AbaiNGDUIDsEnum.DMG: "Доссормунайгаз",
    AbaiNGDUIDsEnum.ZHlMG: "Жылыоймунайгаз",
    AbaiNGDUIDsEnum.ZHMG: "Жайкмунайгаз",
    AbaiNGDUIDsEnum.KMG: "Кайнармунайгаз",
}


def dedup_key(event_code: str, source_type: str, source_id: int | str) -> str:
    """``<event_code>:<source_type>:<source_id>`` — одно событие источника один раз."""
    return f"{event_code}:{source_type}:{source_id}"


def feed_source_id(abai_ngdu_id: int, since: datetime) -> int:
    """Источник сбоя по НГДУ: ``ГГГГММДДЧЧММ`` начала и номер НГДУ.

    Сбой и его окончание получают один источник — одну цепочку, а разные
    сбои одного НГДУ — разные цепочки. Число читается глазами:
    ``202611231020`` + ``12`` → ``20261123102012``.
    """
    return int(f"{since:%Y%m%d%H%M}{abai_ngdu_id:02d}")


def ngdu_name_ru(abai_ngdu_id: int) -> str:
    return NGDU_NAMES_RU.get(abai_ngdu_id, f"НГДУ {abai_ngdu_id}")


def well_title(well_name: str | None, text: str) -> str:
    """«VMB_2051: падение нагрузки»; без имени — только текст."""
    return f"{well_name}: {text}" if well_name else text[:1].upper() + text[1:]


def fmt_dt(moment: datetime | None) -> str:
    """«23.11 10:20» для фактов и текстов; пусто — прочерк."""
    return moment.strftime("%d.%m %H:%M") if moment is not None else "—"


async def emit_all(
    session: AsyncSession,
    items: Iterable[NotificationCreateDTO],
) -> list[int]:
    """Записать пачку в транзакции вызывающего — прямо перед его ``commit``.

    Возвращает id записанных (дубли, выключенный флаг и устаревшие события
    эмиттер отбрасывает). Публикация — ``publish_notifications`` после коммита.
    """
    ids: list[int] = []
    for item in items:
        notification_id = await notification_emitter.emit(session, item)
        if notification_id is not None:
            ids.append(notification_id)
    return ids
