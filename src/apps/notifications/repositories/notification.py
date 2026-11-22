"""Чтение и дозапись журнала уведомлений. Репозитории не коммитят."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ColumnElement, DateTime, Select, exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationRowDTO,
    NotificationViewRowDTO,
)
from apps.notifications.dto.queries.notification import NotificationFilterDTO
from apps.notifications.models.notification import Notification, NotificationView
from shared.repository.sqlalchemy import AsyncAlchemyRepository

_ROW_COLUMNS = (
    Notification.id,
    Notification.event_code,
    Notification.severity,
    Notification.title_ru,
    Notification.body_ru,
    Notification.well_id,
    Notification.abai_ngdu_id,
    Notification.source_type,
    Notification.source_id,
    Notification.payload,
    Notification.event_at,
    Notification.created_at,
    NotificationView.viewed_at,
)


def _rows_query() -> Select[Any]:
    return select(*_ROW_COLUMNS).outerjoin(
        NotificationView,
        NotificationView.notification_id == Notification.id,
    )


def _history_conditions(flt: NotificationFilterDTO) -> list[ColumnElement[bool]]:
    equal_or_bound: tuple[tuple[object, Callable[[Any], ColumnElement[bool]]], ...] = (
        (flt.before_id, lambda v: Notification.id < v),
        (flt.created_from, lambda v: Notification.created_at >= v),
        (flt.created_to, lambda v: Notification.created_at <= v),
        (flt.abai_ngdu_id, lambda v: Notification.abai_ngdu_id == v),
        (flt.well_id, lambda v: Notification.well_id == v),
        (flt.source_type, lambda v: Notification.source_type == v),
        (flt.source_id, lambda v: Notification.source_id == v),
    )
    conditions = [build(value) for value, build in equal_or_bound if value is not None]
    if flt.severities:
        conditions.append(Notification.severity.in_(flt.severities))
    if flt.event_codes:
        conditions.append(Notification.event_code.in_(flt.event_codes))
    if flt.viewed is not None:
        conditions.append(
            NotificationView.id.is_not(None)
            if flt.viewed
            else NotificationView.id.is_(None),
        )
    return conditions


class NotificationRepository(
    AsyncAlchemyRepository[NotificationCreateDTO, BaseModel, Notification],
):
    """Журнал только дописывается: правка и удаление строк запрещены."""

    model = Notification

    async def update(self, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        msg = "notifications journal is append-only"
        raise NotImplementedError(msg)

    async def delete(self, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        msg = "notifications journal is append-only"
        raise NotImplementedError(msg)

    async def insert_ignore(self, values: dict[str, Any]) -> int | None:
        """``INSERT … ON CONFLICT (dedup_key) DO NOTHING RETURNING id``.

        ``None`` — строка с таким ``dedup_key`` уже есть (повторный прогон).
        """
        stmt = (
            pg_insert(Notification)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[Notification.dedup_key])
            .returning(Notification.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_rows(self, flt: NotificationFilterDTO) -> list[NotificationRowDTO]:
        """История: новые сверху, листание назад по ``before_id``."""
        stmt = (
            _rows_query()
            .where(*_history_conditions(flt))
            .order_by(Notification.id.desc())
            .limit(flt.limit)
        )
        return await self._rows(stmt)

    async def get_row(self, notification_id: int) -> NotificationRowDTO | None:
        rows = await self.list_rows_by_ids([notification_id])
        return rows[0] if rows else None

    async def list_rows_by_ids(self, ids: Sequence[int]) -> list[NotificationRowDTO]:
        if not ids:
            return []
        stmt = (
            _rows_query()
            .where(Notification.id.in_(list(ids)))
            .order_by(Notification.id.asc())
        )
        return await self._rows(stmt)

    async def list_chain(
        self,
        source_type: str,
        source_id: int,
    ) -> list[NotificationRowDTO]:
        """Все уведомления одного источника по возрастанию id."""
        stmt = (
            _rows_query()
            .where(
                Notification.source_type == source_type,
                Notification.source_id == source_id,
            )
            .order_by(Notification.id.asc())
        )
        return await self._rows(stmt)

    async def list_ids_created_since(self, since: datetime) -> list[int]:
        """Окно подстраховочного опроса хаба."""
        result = await self.session.execute(
            select(Notification.id)
            .where(Notification.created_at >= since)
            .order_by(Notification.id.asc()),
        )
        return list(result.scalars())

    async def list_snapshot_rows(
        self,
        *,
        after_id: int | None,
        since: datetime,
        latest: int,
        limit: int,
    ) -> list[NotificationRowDTO]:
        """Снимок при подключении, новые сверху.

        ``id > after_id`` (без него — последние ``latest``) плюс всё, записанное
        после ``since``: строка с меньшим id могла закоммититься позже той, что
        клиент уже видел.
        """
        window = select(Notification.id).where(Notification.created_at >= since)
        if after_id is not None:
            head = select(Notification.id).where(Notification.id > after_id)
        else:
            head = (
                select(Notification.id).order_by(Notification.id.desc()).limit(latest)
            )
        ids = window.union(head).subquery()
        stmt = (
            _rows_query()
            .where(Notification.id.in_(select(ids.c.id)))
            .order_by(Notification.id.desc())
            .limit(limit)
        )
        return await self._rows(stmt)

    async def count_unviewed_by_severity(self) -> dict[str, int]:
        result = await self.session.execute(
            select(Notification.severity, func.count())
            .outerjoin(
                NotificationView,
                NotificationView.notification_id == Notification.id,
            )
            .where(NotificationView.id.is_(None))
            .group_by(Notification.severity),
        )
        return dict(result.tuples().all())

    async def is_present(self, notification_id: int) -> bool:
        result = await self.session.execute(
            select(Notification.id).where(Notification.id == notification_id),
        )
        return result.scalar_one_or_none() is not None

    async def _rows(self, stmt: Select[Any]) -> list[NotificationRowDTO]:
        result = await self.session.execute(stmt)
        return [
            NotificationRowDTO.model_validate(dict(row)) for row in result.mappings()
        ]


class NotificationViewRepository(
    AsyncAlchemyRepository[BaseModel, BaseModel, NotificationView],
):
    model = NotificationView

    async def mark_one(
        self,
        notification_id: int,
        viewed_at: datetime,
    ) -> list[NotificationViewRowDTO]:
        """Отметить одно; пусто — уже было просмотрено."""
        return await self._mark(
            select(Notification.id).where(Notification.id == notification_id),
            viewed_at,
        )

    async def mark_up_to(
        self,
        up_to_id: int,
        viewed_at: datetime,
    ) -> list[NotificationViewRowDTO]:
        """Все ещё не просмотренные с ``id <= up_to_id``, без других фильтров."""
        return await self._mark(
            select(Notification.id).where(Notification.id <= up_to_id),
            viewed_at,
        )

    async def list_by_ids(
        self,
        view_ids: Sequence[int],
    ) -> list[NotificationViewRowDTO]:
        if not view_ids:
            return []
        return await self._views(
            select(*self._view_columns()).where(
                NotificationView.id.in_(list(view_ids)),
            ),
        )

    async def list_since(self, since: datetime) -> list[NotificationViewRowDTO]:
        """Окно подстраховочного опроса хаба."""
        return await self._views(
            select(*self._view_columns()).where(NotificationView.viewed_at >= since),
        )

    async def _mark(
        self,
        targets: Select[Any],
        viewed_at: datetime,
    ) -> list[NotificationViewRowDTO]:
        # Уже просмотренные отсекаются сразу; гонку двух экранов закрывает
        # ON CONFLICT.
        source = targets.where(
            ~exists().where(NotificationView.notification_id == Notification.id),
        ).add_columns(literal(viewed_at, DateTime()))
        stmt = (
            pg_insert(NotificationView)
            .from_select(["notification_id", "viewed_at"], source)
            .on_conflict_do_nothing(index_elements=[NotificationView.notification_id])
            .returning(
                NotificationView.id,
                NotificationView.notification_id,
                NotificationView.viewed_at,
            )
        )
        result = await self.session.execute(stmt)
        rows = [
            NotificationViewRowDTO(view_id=r[0], notification_id=r[1], viewed_at=r[2])
            for r in result.all()
        ]
        return sorted(rows, key=lambda row: row.notification_id)

    @staticmethod
    def _view_columns() -> tuple[Any, ...]:
        return (
            NotificationView.id.label("view_id"),
            NotificationView.notification_id,
            NotificationView.viewed_at,
        )

    async def _views(self, stmt: Select[Any]) -> list[NotificationViewRowDTO]:
        result = await self.session.execute(stmt.order_by(NotificationView.id.asc()))
        return [
            NotificationViewRowDTO.model_validate(dict(row))
            for row in result.mappings()
        ]
