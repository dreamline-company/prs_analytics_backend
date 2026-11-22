"""Хаб уведомлений: один на воркер uvicorn, стартует в ``lifespan``.

Сокеты распределены по 3 воркерам, поэтому общий источник — журнал в
Postgres, а Redis только будит хабы:

1. подписка на канал ``notifications``: пришли id — хаб читает строки из
   журнала и рассылает своим сокетам;
2. подстраховка — раз в ``POLL_INTERVAL_SECONDS`` опрос журнала за последние
   ``POLL_WINDOW`` и рассылка того, что ещё не слали. Pub/sub теряет
   сообщение, если Redis моргнул или хаб переподключался. Голый курсор
   ``id > N`` тоже не годится: id раздаётся при вставке, а видна строка после
   коммита — транзакция с id 10 может закоммититься позже транзакции с id 11.
   Поэтому повторы отсекаются по набору отправленных id, а не по максимуму;
3. ``ping`` раз в ``PING_INTERVAL_SECONDS`` со счётчиками непросмотренных —
   прокси не закрывает простаивающий сокет.

Пока сокетов нет, хаб не опрашивает журнал и пропускает сигналы Redis: всё
нужное первый клиент получит снимком. Каждый сокет пишется своей задачей из
своей очереди: медленный или упавший сокет не задерживает остальных.

Отметки «просмотрено» идут тем же путём (``view_ids`` в канале, опрос
таблицы просмотров) и рассылаются кадром ``viewed``.
"""

import asyncio
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Protocol

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.notifications.constants import (
    PING_INTERVAL_SECONDS,
    POLL_INTERVAL_SECONDS,
    POLL_WINDOW,
    REDIS_CHANNEL,
    SNAPSHOT_LATEST,
    SNAPSHOT_MAX,
    local_now,
)
from apps.notifications.dto.internal.notification import NotificationViewRowDTO
from apps.notifications.dto.responses.notification import (
    NotificationItemDTO,
    UnviewedCountsDTO,
)
from apps.notifications.dto.responses.ws import (
    NotificationFrameDTO,
    PingFrameDTO,
    SnapshotFrameDTO,
    ViewedFrameDTO,
)
from apps.notifications.repositories.notification import (
    NotificationRepository,
    NotificationViewRepository,
)
from apps.notifications.services.items import NotificationItemBuilder, unviewed_counts
from apps.notifications.services.publisher import KEY_NOTIFICATIONS, KEY_VIEWS
from core import get_logger

logger = get_logger(__name__)

CLIENT_QUEUE_SIZE = 500
CLIENT_SEND_TIMEOUT_SECONDS = 10.0
REDIS_RETRY_MAX_SECONDS = 30.0
# Коды закрытия сокета хабом: перегрузка/сбой отправки и остановка воркера.
CLOSE_TRY_AGAIN = 1013
CLOSE_GOING_AWAY = 1001


class HubStore(Protocol):
    """Чтение журнала, которое нужно хабу."""

    async def items_by_ids(self, ids: Sequence[int]) -> list[NotificationItemDTO]: ...

    async def ids_created_since(self, since: datetime) -> list[int]: ...

    async def snapshot(
        self,
        *,
        after_id: int | None,
        since: datetime,
    ) -> list[NotificationItemDTO]: ...

    async def views_by_ids(
        self,
        view_ids: Sequence[int],
    ) -> list[NotificationViewRowDTO]: ...

    async def views_since(self, since: datetime) -> list[NotificationViewRowDTO]: ...

    async def unviewed(self) -> UnviewedCountsDTO: ...


class HubMessageSource(Protocol):
    """Сигналы о новых строках. Обрыв связи — исключение из ``listen``."""

    def listen(self) -> AsyncIterator[dict[str, Any]]: ...


class WebSocketLike(Protocol):
    async def send_json(self, data: Any) -> None: ...  # noqa: ANN401

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class SessionHubStore:
    """``HubStore`` поверх журнала: короткая сессия на каждый запрос."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def items_by_ids(self, ids: Sequence[int]) -> list[NotificationItemDTO]:
        async with self._session_maker() as session:
            rows = await NotificationRepository(session).list_rows_by_ids(ids)
            return await NotificationItemBuilder.for_session(session).build(rows)

    async def ids_created_since(self, since: datetime) -> list[int]:
        async with self._session_maker() as session:
            return await NotificationRepository(session).list_ids_created_since(since)

    async def snapshot(
        self,
        *,
        after_id: int | None,
        since: datetime,
    ) -> list[NotificationItemDTO]:
        async with self._session_maker() as session:
            rows = await NotificationRepository(session).list_snapshot_rows(
                after_id=after_id,
                since=since,
                latest=SNAPSHOT_LATEST,
                limit=SNAPSHOT_MAX,
            )
            return await NotificationItemBuilder.for_session(session).build(rows)

    async def views_by_ids(
        self,
        view_ids: Sequence[int],
    ) -> list[NotificationViewRowDTO]:
        async with self._session_maker() as session:
            return await NotificationViewRepository(session).list_by_ids(view_ids)

    async def views_since(self, since: datetime) -> list[NotificationViewRowDTO]:
        async with self._session_maker() as session:
            return await NotificationViewRepository(session).list_since(since)

    async def unviewed(self) -> UnviewedCountsDTO:
        async with self._session_maker() as session:
            counts = await NotificationRepository(session).count_unviewed_by_severity()
            return unviewed_counts(counts)


class RedisMessageSource:
    """Подписка на канал Redis; JSON-сообщения публикатора."""

    def __init__(
        self,
        url: str,
        channel: str = REDIS_CHANNEL,
        *,
        read_timeout: float = 5.0,
    ) -> None:
        self._url = url
        self._channel = channel
        self._read_timeout = read_timeout

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        client = redis.Redis.from_url(
            self._url,
            socket_connect_timeout=5,
            socket_keepalive=True,
            # PING на простаивающем соединении: мёртвый сокет всплывает
            # ошибкой, и хаб переподключается.
            health_check_interval=15,
        )
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(self._channel)
            logger.info("Notifications hub subscribed to Redis %s", self._channel)
            while True:
                message = await pubsub.get_message(timeout=self._read_timeout)
                if message is None:
                    continue
                payload = _decode_message(message.get("data"))
                if payload is not None:
                    yield payload
        finally:
            with suppress(Exception):
                await pubsub.aclose()
            with suppress(Exception):
                await client.aclose()


def _decode_message(data: object) -> dict[str, Any] | None:
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if not isinstance(data, str):
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        logger.warning("Notifications hub: bad Redis message %r", data[:200])
        return None
    return payload if isinstance(payload, dict) else None


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int) and not isinstance(v, bool)]


class HubClient:
    """Один сокет: своя очередь кадров и своя задача-писатель."""

    def __init__(
        self,
        ws: WebSocketLike,
        *,
        queue_size: int = CLIENT_QUEUE_SIZE,
        send_timeout: float = CLIENT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_size,
        )
        self._send_timeout = send_timeout
        # Код, с которым писатель закроет сокет; None — сокет закрывает
        # обработчик WS (клиент ушёл сам).
        self._close_code: int | None = None
        self.closed = False

    def push(self, frame: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Сокет не успевает: закрываем, клиент переподключится и доберёт
            # пропущенное снимком по after_id.
            logger.warning("Notifications client queue overflow; dropping socket")
            self.close(code=CLOSE_TRY_AGAIN)

    def close(self, code: int | None = None) -> None:
        """Остановить писателя; с ``code`` он ещё и закроет сокет."""
        if self.closed:
            return
        self.closed = True
        self._close_code = code
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)

    async def run(self) -> None:
        """Шлёт кадры по порядку; сбой или таймаут отправки закрывает сокет."""
        try:
            while True:
                frame = await self._queue.get()
                if frame is None:
                    break
                await asyncio.wait_for(
                    self._ws.send_json(frame),
                    timeout=self._send_timeout,
                )
        except asyncio.CancelledError:
            self.closed = True
            raise
        except Exception as e:  # noqa: BLE001 — любой сбой сокета закрывает только его
            logger.info("Notifications client send failed: %r", e)
            self.closed = True
            self._close_code = CLOSE_TRY_AGAIN
        if self._close_code is not None:
            with suppress(Exception):
                await self._ws.close(code=self._close_code)


def _dump(frame: Any) -> dict[str, Any]:  # noqa: ANN401 — любой кадр pydantic
    return frame.model_dump(mode="json")


class NotificationHub:
    def __init__(  # noqa: PLR0913
        self,
        store: HubStore,
        source: HubMessageSource | None,
        *,
        clock: Callable[[], datetime] = local_now,
        monotonic: Callable[[], float] = time.monotonic,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        window: timedelta = POLL_WINDOW,
        ping_interval: float = PING_INTERVAL_SECONDS,
        redis_retry_max: float = REDIS_RETRY_MAX_SECONDS,
    ) -> None:
        self._store = store
        self._source = source
        self._clock = clock
        self._monotonic = monotonic
        self._poll_interval = poll_interval
        self._window = window
        self._ping_interval = ping_interval
        self._redis_retry_max = redis_retry_max
        self._clients: set[HubClient] = set()
        # Рассылка и снимок под одним замком: строка, разосланная до снимка,
        # в снимке есть, разосланная после — дойдёт до нового клиента.
        self._lock = asyncio.Lock()
        # id → когда отправлено (monotonic); чистятся через 2 окна.
        self._sent: dict[int, float] = {}
        self._sent_views: dict[int, float] = {}
        self._unviewed = UnviewedCountsDTO()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def client_count(self) -> int:
        return sum(1 for c in self._clients if not c.closed)

    async def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name="notifications-poll"),
            asyncio.create_task(self._ping_loop(), name="notifications-ping"),
        ]
        if self._source is not None:
            self._tasks.append(
                asyncio.create_task(self._redis_loop(), name="notifications-redis"),
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        for client in list(self._clients):
            client.close(code=CLOSE_GOING_AWAY)
        self._clients.clear()

    async def attach(self, client: HubClient, after_id: int | None) -> None:
        """Ответ на ``hello``: снимок, затем живые кадры.

        Первый клиент воркера засевает набор отправленного тем, что ушло в
        снимок: пока сокетов не было, хаб ничего не слал, и иначе опрос
        повторил бы снимок кадрами ``notification``.
        """
        async with self._lock:
            since = self._clock() - self._window
            first = not any(
                other is not client and not other.closed for other in self._clients
            )
            # Просмотры — до снимка: отметка между запросами окажется в снимке
            # и не потеряется (в худшем случае придёт ещё и кадром viewed).
            views = await self._store.views_since(since) if first else []
            items = await self._store.snapshot(after_id=after_id, since=since)
            self._unviewed = await self._store.unviewed()
            if first:
                now = self._monotonic()
                for item in items:
                    if item.created_at >= since:
                        self._sent.setdefault(item.id, now)
                for view in views:
                    self._sent_views.setdefault(view.view_id, now)
            client.push(
                _dump(SnapshotFrameDTO(items=items, unviewed=self._unviewed)),
            )
            self._clients.add(client)

    def detach(self, client: HubClient) -> None:
        self._clients.discard(client)
        client.close()

    async def handle_message(self, message: dict[str, Any]) -> None:
        ids = _int_list(message.get(KEY_NOTIFICATIONS))
        if ids:
            await self.deliver_notifications(ids)
        view_ids = _int_list(message.get(KEY_VIEWS))
        if view_ids:
            await self.deliver_views(view_ids=view_ids)

    async def deliver_notifications(self, ids: Sequence[int]) -> None:
        async with self._lock:
            if self.client_count == 0:
                return
            fresh = sorted({i for i in ids if i not in self._sent})
            if not fresh:
                return
            # Не найденный id (строку ещё не видно) не помечается отправленным:
            # его подберёт следующий опрос.
            items = await self._store.items_by_ids(fresh)
            now = self._monotonic()
            for item in sorted(items, key=lambda i: i.id):
                self._sent[item.id] = now
                self._broadcast(_dump(NotificationFrameDTO(item=item)))

    async def deliver_views(
        self,
        *,
        view_ids: Sequence[int] | None = None,
        rows: Sequence[NotificationViewRowDTO] | None = None,
    ) -> None:
        async with self._lock:
            if self.client_count == 0:
                return
            if rows is None:
                fresh_ids = [v for v in view_ids or () if v not in self._sent_views]
                if not fresh_ids:
                    return
                rows = await self._store.views_by_ids(fresh_ids)
            fresh = [r for r in rows if r.view_id not in self._sent_views]
            if not fresh:
                return
            self._unviewed = await self._store.unviewed()
            now = self._monotonic()
            by_time: dict[datetime, list[int]] = defaultdict(list)
            for row in fresh:
                self._sent_views[row.view_id] = now
                by_time[row.viewed_at].append(row.notification_id)
            for viewed_at in sorted(by_time):
                self._broadcast(
                    _dump(
                        ViewedFrameDTO(
                            ids=sorted(by_time[viewed_at]),
                            viewed_at=viewed_at,
                            unviewed=self._unviewed,
                        ),
                    ),
                )

    async def poll_once(self) -> None:
        """Подстраховка: всё из окна, что ещё не слали."""
        if self.client_count == 0:
            return
        since = self._clock() - self._window
        await self.deliver_notifications(await self._store.ids_created_since(since))
        await self.deliver_views(rows=await self._store.views_since(since))
        self._prune()

    async def ping_once(self) -> None:
        if self.client_count == 0:
            return
        try:
            self._unviewed = await self._store.unviewed()
        except Exception:  # noqa: BLE001 — ping уходит с последним известным счётом
            logger.warning("Notifications unviewed count failed", exc_info=True)
        self._broadcast(_dump(PingFrameDTO(unviewed=self._unviewed)))

    def _broadcast(self, frame: dict[str, Any]) -> None:
        for client in list(self._clients):
            if client.closed:
                self._clients.discard(client)
                continue
            client.push(frame)

    def _prune(self) -> None:
        cutoff = self._monotonic() - 2 * self._window.total_seconds()
        for sent in (self._sent, self._sent_views):
            for key in [k for k, at in sent.items() if at < cutoff]:
                del sent[key]

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Notifications poll failed")

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            try:
                await self.ping_once()
            except Exception:
                logger.exception("Notifications ping failed")

    async def _redis_loop(self) -> None:
        """Подписка с переподключением 1 → 30 с; опрос в это время работает."""
        if self._source is None:
            return
        delay = 1.0
        while True:
            try:
                async for message in self._source.listen():
                    delay = 1.0
                    try:
                        await self.handle_message(message)
                    except Exception:
                        logger.exception("Notifications Redis message failed")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — любой обрыв Redis: переподключение
                logger.warning(
                    "Notifications Redis subscription lost: %r; retry in %.0f s",
                    e,
                    delay,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._redis_retry_max)


def build_notification_hub() -> NotificationHub:
    """Хаб воркера на журнале приложения и Redis приложения."""
    from core.settings import get_settings  # noqa: PLC0415
    from shared.database.sql.setup import session_makers  # noqa: PLC0415

    return NotificationHub(
        store=SessionHubStore(session_makers["app"]),
        source=RedisMessageSource(get_settings().APP_REDIS_DB_URL),
    )
