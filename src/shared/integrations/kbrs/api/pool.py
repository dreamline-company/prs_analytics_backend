"""Async pool of pre-logged-in Toucan clients.

``ToucanBackendClient`` (with its underlying ``http.client.HTTPConnection``)
is *not* thread-safe: two coroutines running RPCs on the same client via
``asyncio.to_thread`` would interleave bytes on one shared socket.

This pool holds N independent clients — each with its own socket and its own
Toucan session — inside an ``asyncio.Queue``. Callers acquire a client for
the duration of one RPC and release it back.

Recycling: when a ``ToucanTransportError`` escapes the ``acquire()`` block,
the pool treats the client as dead — closes it and boots a fresh one to
replace it in the queue. Other exception types (decode errors, business
errors) don't touch the client; it goes back to the pool as-is.

``call_with_retry`` is a thin convenience for the common one-shot case:
run one function against a pooled client with automatic re-acquire and one
extra attempt on transport failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TypeVar

from core import get_logger

from .client import ToucanBackendClient
from .config import ToucanClientConfig
from .dtos import ToucanCredentialsDto
from .exceptions import ToucanApiError, ToucanTransportError

logger = get_logger(__name__)

T = TypeVar("T")


class ToucanClientPool:
    def __init__(
        self,
        clients: list[ToucanBackendClient],
        *,
        config: ToucanClientConfig,
        credentials: ToucanCredentialsDto,
    ) -> None:
        self._config = config
        self._credentials = credentials
        self._q: asyncio.Queue[ToucanBackendClient] = asyncio.Queue()
        for c in clients:
            self._q.put_nowait(c)
        self._size = len(clients)
        self._closed = False
        # Monotonic counter for unique machine_unique on re-logins.
        self._boot_counter = len(clients)

    @property
    def size(self) -> int:
        return self._size

    @classmethod
    async def create(
        cls,
        *,
        size: int,
        config: ToucanClientConfig,
        credentials: ToucanCredentialsDto,
    ) -> ToucanClientPool:
        if size < 1:
            msg = f"Pool size must be >= 1, got {size}"
            raise ValueError(msg)

        logger.info("Booting Toucan client pool of size %s.", size)

        def _boot_one(worker_id: int) -> ToucanBackendClient:
            client = ToucanBackendClient(config)
            client.login(_creds_with_id(credentials, worker_id))
            return client

        clients = await asyncio.gather(
            *(asyncio.to_thread(_boot_one, i) for i in range(size)),
        )
        return cls(list(clients), config=config, credentials=credentials)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[ToucanBackendClient]:
        """Yield a client for one operation.

        On ``ToucanTransportError`` the client is considered dead: it's closed
        and replaced in the pool by a freshly-logged-in client. The exception
        still propagates — callers decide whether to retry.
        """

        if self._closed:
            msg = "ToucanClientPool is closed"
            raise ToucanApiError(msg)

        client = await self._q.get()
        transport_broken = False
        try:
            yield client
        except ToucanTransportError:
            transport_broken = True
            raise
        finally:
            if transport_broken:
                await self._recycle(client)
            elif self._closed:
                await asyncio.to_thread(_safe_close, client)
            else:
                self._q.put_nowait(client)

    async def call_with_retry(
        self,
        fn: Callable[[ToucanBackendClient], T],
        *,
        retries: int = 1,
    ) -> T:
        """Run ``fn(client)`` in a thread against a pooled client.

        On ``ToucanTransportError`` the offending client is recycled and the
        call is retried up to ``retries`` extra times against fresh clients.
        """

        attempts = retries + 1
        last_error: ToucanTransportError | None = None
        for attempt in range(attempts):
            try:
                async with self.acquire() as client:
                    return await asyncio.to_thread(fn, client)
            except ToucanTransportError as exc:
                last_error = exc
                logger.warning(
                    "Toucan RPC transport error (attempt %s/%s): %s",
                    attempt + 1,
                    attempts,
                    exc,
                )
        assert last_error is not None
        raise last_error

    async def keepalive_all(self, fn: Callable[[ToucanBackendClient], object]) -> int:
        """Ping every currently idle client to keep its Toucan session alive.

        Drains the idle queue, runs ``fn`` on each drained client concurrently
        (in threads), then returns healthy clients to the pool. A client whose
        ping raises ``ToucanTransportError`` is recycled — closed and replaced
        by a freshly-logged-in one. Clients checked out by other coroutines at
        call time are skipped: they are mid-RPC and thus already alive.

        Returns the number of successfully pinged clients.
        """

        if self._closed:
            return 0

        idle: list[ToucanBackendClient] = []
        while True:
            try:
                idle.append(self._q.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not idle:
            return 0

        async def _ping(client: ToucanBackendClient) -> bool:
            try:
                await asyncio.to_thread(fn, client)
            except ToucanTransportError as exc:
                logger.warning("Keepalive ping failed; recycling client: %s", exc)
                await self._recycle(client)
                return False
            except Exception:  # noqa: BLE001
                # Не-транспортная ошибка не означает, что сессия мертва.
                logger.warning(
                    "Keepalive ping raised a non-transport error.",
                    exc_info=True,
                )
                self._q.put_nowait(client)
                return True
            else:
                self._q.put_nowait(client)
                return True

        results = await asyncio.gather(*(_ping(client) for client in idle))
        return sum(results)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        drained: list[ToucanBackendClient] = []
        while True:
            try:
                drained.append(self._q.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not drained:
            return
        await asyncio.gather(
            *(asyncio.to_thread(_safe_close, c) for c in drained),
        )
        logger.info("Toucan client pool closed (%s clients).", len(drained))

    async def _recycle(self, dead: ToucanBackendClient) -> None:
        await asyncio.to_thread(_safe_close, dead)
        if self._closed:
            return
        self._boot_counter += 1
        worker_id = self._boot_counter
        try:
            fresh = await asyncio.to_thread(self._boot_one, worker_id)
        except Exception:
            logger.exception(
                "Failed to re-login Toucan client during recycle; pool shrank to %s.",
                max(self._size - 1, 0),
            )
            self._size -= 1
            return
        self._q.put_nowait(fresh)
        logger.info("Recycled Toucan pool client (worker_id=%s).", worker_id)

    def _boot_one(self, worker_id: int) -> ToucanBackendClient:
        client = ToucanBackendClient(self._config)
        client.login(_creds_with_id(self._credentials, worker_id))
        return client


def _creds_with_id(
    credentials: ToucanCredentialsDto,
    worker_id: int,
) -> ToucanCredentialsDto:
    tag = f"PY-POOL-{worker_id}"
    return replace(
        credentials,
        machine_unique=tag,
        computer_name=tag,
        host_name=tag,
    )


def _safe_close(client: ToucanBackendClient) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001
        logger.debug("Ignoring error while closing Toucan client.", exc_info=True)
