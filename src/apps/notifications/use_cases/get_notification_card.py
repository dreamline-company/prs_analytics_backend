"""Подробная карточка: элемент, факты из payload и цепочка источника.

GET отметку «просмотрено» не ставит — её фронт шлёт отдельно.
"""

from apps.notifications.dto.responses.notification import NotificationCardDTO
from apps.notifications.repositories.notification import NotificationRepository
from apps.notifications.services.items import NotificationItemBuilder


class GetNotificationCardUseCase:
    def __init__(
        self,
        *,
        notification_repository: NotificationRepository,
        item_builder: NotificationItemBuilder,
    ) -> None:
        self.notification_repository = notification_repository
        self.item_builder = item_builder

    async def execute(self, notification_id: int) -> NotificationCardDTO | None:
        row = await self.notification_repository.get_row(notification_id)
        if row is None:
            return None
        chain = []
        if row.source_type is not None and row.source_id is not None:
            chain = await self.notification_repository.list_chain(
                row.source_type,
                row.source_id,
            )
        return await self.item_builder.card(row, chain)
