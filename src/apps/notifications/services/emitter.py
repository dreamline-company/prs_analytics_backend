"""Запись уведомления в журнал — точка входа для производителей.

Как вызывать из таски::

    ids = []
    async with session_makers["app"]() as session:
        ...  # своя работа таски
        ids.append(await emitter.emit(session, NotificationCreateDTO(...)))
        await session.commit()
    await publish_notifications(ids)

Эмиттер не коммитит: строка уходит в журнал в той же транзакции, что и
событие. Публикация — после коммита; потерянный сигнал подберёт опрос хаба.

**emit — прямо перед commit.** ``created_at`` ставится при INSERT, а видна
строка после коммита. Подстраховочный опрос хаба и окно снимка смотрят на
``created_at`` за последние 15 мин: строка, закоммиченная позже, доедет
только сигналом Redis, а потерянный сигнал её уже не спасёт. Поэтому
транзакция от emit до commit должна быть заметно короче окна.

Запись идёт в savepoint: сбой вставки (например, несуществующая скважина)
откатывает только уведомление, а работа производителя остаётся в
транзакции. Эмиттер тогда пишет ошибку в лог и возвращает ``None``.

Антиспам: пересборки истории (restage, recheck, backfill) уведомлений не
шлют — производители вызывают эмиттер только из живых прогонов. Страховка
здесь: событие старше ``MAX_EVENT_AGE`` не пишется. Выключенный флаг
``NOTIFICATIONS_ENABLED`` делает эмиттер пустышкой.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.constants import MAX_EVENT_AGE, local_now
from apps.notifications.dto.internal.notification import NotificationCreateDTO
from apps.notifications.repositories.notification import NotificationRepository
from apps.org.repositories.org import OrgRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_org import WellOrgRepository
from core import get_logger
from core.settings import get_settings

logger = get_logger(__name__)


class NotificationEmitter:
    def __init__(
        self,
        *,
        enabled: bool | None = None,
        clock: Callable[[], datetime] = local_now,
        max_event_age: timedelta = MAX_EVENT_AGE,
    ) -> None:
        # None — читать флаг из настроек на каждый вызов.
        self._enabled = enabled
        self._clock = clock
        self._max_event_age = max_event_age

    @property
    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        return get_settings().NOTIFICATIONS_ENABLED

    async def emit(
        self,
        session: AsyncSession,
        data: NotificationCreateDTO,
    ) -> int | None:
        """id новой строки; ``None`` — выключено, устарело или дубль."""
        if not self.enabled:
            logger.debug("Notifications disabled; skipped %s", data.dedup_key)
            return None
        now = self._clock()
        if data.event_at < now - self._max_event_age:
            logger.info(
                "Notification %s skipped: event_at %s older than %s",
                data.dedup_key,
                data.event_at,
                self._max_event_age,
            )
            return None

        try:
            async with session.begin_nested():
                notification_id = await self._insert(session, data, now)
        except SQLAlchemyError:
            logger.exception("Notification %s not written", data.dedup_key)
            return None
        if notification_id is None:
            logger.debug("Notification %s already exists", data.dedup_key)
        return notification_id

    async def _insert(
        self,
        session: AsyncSession,
        data: NotificationCreateDTO,
        now: datetime,
    ) -> int | None:
        abai_ngdu_id = data.abai_ngdu_id
        if abai_ngdu_id is None and data.well_id is not None:
            abai_ngdu_id = await self._resolve_abai_ngdu_id(session, data.well_id)
        return await NotificationRepository(session).insert_ignore(
            {
                "event_code": data.event_code,
                "severity": data.severity,
                "title_ru": data.title_ru,
                "body_ru": data.body_ru,
                "well_id": data.well_id,
                "abai_ngdu_id": abai_ngdu_id,
                "source_type": data.source_type,
                "source_id": data.source_id,
                "dedup_key": data.dedup_key,
                "payload": data.payload_with_facts(),
                "event_at": data.event_at,
                "created_at": now,
            },
        )

    @staticmethod
    async def _resolve_abai_ngdu_id(session: AsyncSession, well_id: int) -> int | None:
        """Текущее НГДУ скважины (ABAI-id) по зеркалу well_org."""
        well = await WellRepository(session).get_by_id(well_id)
        if well is None or well.abai_id is None:
            return None
        org = await GetNGDUForWellUseCase(
            well_org_repository=WellOrgRepository(session),
            org_repository=OrgRepository(session),
        ).execute(well.abai_id)
        return org.abai_id if org is not None else None


notification_emitter = NotificationEmitter()
