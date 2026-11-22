from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.notifications.constants import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    MAX_ID,
)
from apps.notifications.dto.queries.notification import ListNotificationsQuery
from apps.notifications.dto.responses.notification import (
    NotificationCardResponseDTO,
    NotificationHistoryResponseDTO,
    NotificationViewResponseDTO,
)
from apps.notifications.repositories.notification import (
    NotificationRepository,
    NotificationViewRepository,
)
from apps.notifications.services.items import NotificationItemBuilder
from apps.notifications.use_cases.get_notification_card import (
    GetNotificationCardUseCase,
)
from apps.notifications.use_cases.list_notifications import ListNotificationsUseCase
from apps.notifications.use_cases.mark_viewed import (
    MarkNotificationsViewedUseCase,
    NotificationNotFoundError,
)
from apps.org.repositories.org import OrgRepository
from shared.dependencies.db import get_app_session

router = APIRouter(prefix="/notifications", tags=["notifications"])

NOT_FOUND = "Notification not found"


def _split(values: list[str] | None) -> list[str]:
    """``alarm,warning`` и повтор параметра — одинаково."""
    out: list[str] = []
    for value in values or ():
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _mark_use_case(session: AsyncSession) -> MarkNotificationsViewedUseCase:
    return MarkNotificationsViewedUseCase(
        session=session,
        notification_repository=NotificationRepository(session),
        view_repository=NotificationViewRepository(session),
    )


@router.get("")
async def list_notifications(  # noqa: PLR0913
    session: Annotated[AsyncSession, Depends(get_app_session)],
    before_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=HISTORY_MAX_LIMIT),
    ] = HISTORY_DEFAULT_LIMIT,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    severity: Annotated[
        list[str] | None,
        Query(description="info / warning / alarm, можно через запятую"),
    ] = None,
    event_code: Annotated[
        list[str] | None,
        Query(description="можно через запятую"),
    ] = None,
    viewed: Annotated[bool | None, Query()] = None,
    ngdu_id: Annotated[
        int | None,
        Query(ge=1, le=MAX_ID, description="org.id из /org/v1/ngdu"),
    ] = None,
    well_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    source_type: Annotated[str | None, Query(max_length=30)] = None,
    source_id: Annotated[int | None, Query(ge=0, le=MAX_ID)] = None,
) -> NotificationHistoryResponseDTO:
    try:
        query = ListNotificationsQuery(
            before_id=before_id,
            limit=limit,
            created_from=created_from,
            created_to=created_to,
            severities=_split(severity),
            event_codes=_split(event_code),
            viewed=viewed,
            ngdu_id=ngdu_id,
            well_id=well_id,
            source_type=source_type,
            source_id=source_id,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=e.errors(include_url=False, include_context=False),
        ) from e
    use_case = ListNotificationsUseCase(
        notification_repository=NotificationRepository(session),
        org_repository=OrgRepository(session),
        item_builder=NotificationItemBuilder.for_session(session),
    )
    return NotificationHistoryResponseDTO(data=await use_case.execute(query))


@router.post("/view-all")
async def view_all_notifications(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    up_to_id: Annotated[
        int,
        Query(ge=1, le=MAX_ID, description="самый большой id, который видит экран"),
    ],
) -> NotificationViewResponseDTO:
    result = await _mark_use_case(session).mark_up_to(up_to_id)
    return NotificationViewResponseDTO(data=result)


@router.get("/{notification_id}")
async def get_notification(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    notification_id: Annotated[int, Path(ge=1, le=MAX_ID)],
) -> NotificationCardResponseDTO:
    use_case = GetNotificationCardUseCase(
        notification_repository=NotificationRepository(session),
        item_builder=NotificationItemBuilder.for_session(session),
    )
    card = await use_case.execute(notification_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return NotificationCardResponseDTO(data=card)


@router.post("/{notification_id}/view")
async def view_notification(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    notification_id: Annotated[int, Path(ge=1, le=MAX_ID)],
) -> NotificationViewResponseDTO:
    try:
        result = await _mark_use_case(session).mark_one(notification_id)
    except NotificationNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=NOT_FOUND,
        ) from e
    return NotificationViewResponseDTO(data=result)
