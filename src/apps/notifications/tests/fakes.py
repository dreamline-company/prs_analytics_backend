"""Подделки для тестов хаба: журнал в памяти с управляемой «видимостью»."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from typing import Any

from apps.notifications.dto.internal.notification import NotificationViewRowDTO
from apps.notifications.dto.responses.notification import (
    NotificationItemDTO,
    UnviewedCountsDTO,
)

NOW = datetime(2026, 11, 22, 12, 0, 0)  # noqa: DTZ001 — местное наивное


def item(
    notification_id: int,
    *,
    created_at: datetime | None = None,
    severity: str = "alarm",
) -> NotificationItemDTO:
    created = created_at or NOW
    return NotificationItemDTO(
        id=notification_id,
        event_code="system.test",
        severity=severity,
        title_ru=f"#{notification_id}",
        body_ru=None,
        event_at=created,
        created_at=created,
        viewed_at=None,
        source_type=None,
        source_id=None,
        well=None,
    )


class FakeStore:
    """Журнал, где строка видна только после «коммита» (``commit``)."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.visible: dict[int, NotificationItemDTO] = {}
        self.views: dict[int, NotificationViewRowDTO] = {}
        self.calls: list[str] = []
        self.fail_unviewed = False

    def commit(self, *items: NotificationItemDTO) -> None:
        for it in items:
            self.visible[it.id] = it

    def view(self, view_id: int, notification_id: int, viewed_at: datetime) -> None:
        self.views[view_id] = NotificationViewRowDTO(
            view_id=view_id,
            notification_id=notification_id,
            viewed_at=viewed_at,
        )

    async def items_by_ids(self, ids: Sequence[int]) -> list[NotificationItemDTO]:
        self.calls.append("items_by_ids")
        return [self.visible[i] for i in sorted(ids) if i in self.visible]

    async def ids_created_since(self, since: datetime) -> list[int]:
        self.calls.append("ids_created_since")
        return sorted(i for i, it in self.visible.items() if it.created_at >= since)

    async def snapshot(
        self,
        *,
        after_id: int | None,
        since: datetime,
    ) -> list[NotificationItemDTO]:
        self.calls.append(f"snapshot:{after_id}")
        ids = {i for i, it in self.visible.items() if it.created_at >= since}
        if after_id is not None:
            ids |= {i for i in self.visible if i > after_id}
        else:
            ids |= set(sorted(self.visible, reverse=True)[:50])
        return [self.visible[i] for i in sorted(ids, reverse=True)]

    async def views_by_ids(
        self,
        view_ids: Sequence[int],
    ) -> list[NotificationViewRowDTO]:
        return [self.views[v] for v in sorted(view_ids) if v in self.views]

    async def views_since(self, since: datetime) -> list[NotificationViewRowDTO]:
        return [v for _, v in sorted(self.views.items()) if v.viewed_at >= since]

    async def unviewed(self) -> UnviewedCountsDTO:
        if self.fail_unviewed:
            msg = "db down"
            raise RuntimeError(msg)
        viewed = {v.notification_id for v in self.views.values()}
        counts = {"alarm": 0, "warning": 0, "info": 0}
        for i, it in self.visible.items():
            if i not in viewed:
                counts[it.severity] += 1
        return UnviewedCountsDTO(**counts)


class FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.frames: list[dict[str, Any]] = []
        self.fail = fail
        self.closed_with: int | None = None

    async def send_json(self, data: Any) -> None:  # noqa: ANN401
        if self.fail:
            msg = "socket gone"
            raise ConnectionError(msg)
        self.frames.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:  # noqa: ARG002
        self.closed_with = code

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]

    def notification_ids(self) -> list[int]:
        return [f["item"]["id"] for f in self.frames if f["type"] == "notification"]


class BrokenSource:
    """Redis недоступен: каждая попытка подписки падает."""

    def __init__(self) -> None:
        self.attempts = 0

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        self.attempts += 1
        msg = "Redis unavailable"
        raise ConnectionError(msg)
        yield {}  # pragma: no cover — делает метод генератором


class QueueSource:
    """Redis в памяти: сообщения кладутся в очередь."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.queue.get()


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)
