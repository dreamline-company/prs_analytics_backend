"""История уведомлений с фильтрами, новые сверху."""

from apps.notifications.dto.queries.notification import (
    ListNotificationsQuery,
    NotificationFilterDTO,
)
from apps.notifications.dto.responses.notification import NotificationHistoryDTO
from apps.notifications.repositories.notification import NotificationRepository
from apps.notifications.services.items import NotificationItemBuilder
from apps.org.repositories.org import OrgRepository


class ListNotificationsUseCase:
    def __init__(
        self,
        *,
        notification_repository: NotificationRepository,
        org_repository: OrgRepository,
        item_builder: NotificationItemBuilder,
    ) -> None:
        self.notification_repository = notification_repository
        self.org_repository = org_repository
        self.item_builder = item_builder

    async def execute(self, query: ListNotificationsQuery) -> NotificationHistoryDTO:
        abai_ngdu_id = None
        if query.ngdu_id is not None:
            # Фронт знает НГДУ по org.id (/org/v1/ngdu), журнал — по ABAI-id.
            org = await self.org_repository.get_by_id(query.ngdu_id)
            if org is None:
                return NotificationHistoryDTO(items=[], next_before_id=None)
            abai_ngdu_id = org.abai_id

        # Строка сверх лимита говорит, есть ли следующая страница.
        rows = await self.notification_repository.list_rows(
            NotificationFilterDTO(
                **query.model_dump(exclude={"ngdu_id", "limit"}),
                limit=query.limit + 1,
                abai_ngdu_id=abai_ngdu_id,
            ),
        )
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        items = await self.item_builder.build(rows)
        return NotificationHistoryDTO(
            items=items,
            next_before_id=items[-1].id if has_more and items else None,
        )
