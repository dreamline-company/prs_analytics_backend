"""Отметка «просмотрено» — общая на все экраны.

Коммит здесь: use case — граница транзакции HTTP-запроса. После коммита
id отметок уходят в Redis, хабы рассылают кадр ``viewed``.
"""

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.constants import local_now
from apps.notifications.dto.internal.notification import NotificationViewRowDTO
from apps.notifications.dto.responses.notification import NotificationViewResultDTO
from apps.notifications.repositories.notification import (
    NotificationRepository,
    NotificationViewRepository,
)
from apps.notifications.services.items import unviewed_counts
from apps.notifications.services.publisher import publish_views

PublishViews = Callable[[Iterable[int | None]], Awaitable[None]]


class NotificationNotFoundError(LookupError):
    pass


class MarkNotificationsViewedUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        notification_repository: NotificationRepository,
        view_repository: NotificationViewRepository,
        publish: PublishViews = publish_views,
        clock: Callable[[], datetime] = local_now,
    ) -> None:
        self.session = session
        self.notification_repository = notification_repository
        self.view_repository = view_repository
        self.publish = publish
        self.clock = clock

    async def mark_one(self, notification_id: int) -> NotificationViewResultDTO:
        if not await self.notification_repository.is_present(notification_id):
            raise NotificationNotFoundError(notification_id)
        rows = await self.view_repository.mark_one(notification_id, self.clock())
        return await self._finish(rows)

    async def mark_up_to(self, up_to_id: int) -> NotificationViewResultDTO:
        """Все ещё не просмотренные с ``id <= up_to_id``, без учёта фильтров.

        Пришедшее после нажатия (id больше) не задевается.
        """
        rows = await self.view_repository.mark_up_to(up_to_id, self.clock())
        return await self._finish(rows)

    async def _finish(
        self,
        rows: list[NotificationViewRowDTO],
    ) -> NotificationViewResultDTO:
        counts = await self.notification_repository.count_unviewed_by_severity()
        await self.session.commit()
        await self.publish(row.view_id for row in rows)
        return NotificationViewResultDTO(
            ids=[row.notification_id for row in rows],
            viewed_at=rows[0].viewed_at if rows else None,
            unviewed=unviewed_counts(counts),
        )
