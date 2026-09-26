"""Пул Toucan: после неудачных перелогинов не виснет и дорастает по требованию."""

from __future__ import annotations

import asyncio

import pytest

from shared.integrations.kbrs.api.exceptions import ToucanTransportError
from shared.integrations.kbrs.api.pool import ToucanClientPool


class _Dummy:
    def close(self) -> None:
        pass


def _pool_with_flaky_login() -> tuple[ToucanClientPool, dict[str, bool]]:
    pool = ToucanClientPool([_Dummy(), _Dummy()], config=None, credentials=None)  # type: ignore[arg-type]
    toucan_up = {"v": False}

    def boot(_worker_id: int) -> _Dummy:
        if not toucan_up["v"]:
            msg = "toucan down"
            raise ConnectionError(msg)
        return _Dummy()

    pool._boot_one = boot  # type: ignore[method-assign]  # noqa: SLF001
    return pool, toucan_up


async def _use_and_break(pool: ToucanClientPool) -> None:
    async with pool.acquire():
        msg = "boom"
        raise ToucanTransportError(msg)


async def _break_one(pool: ToucanClientPool) -> None:
    with pytest.raises(ToucanTransportError):
        await _use_and_break(pool)


def test_empty_pool_raises_instead_of_hanging_and_regrows_when_backend_returns() -> (
    None
):
    async def scenario() -> None:
        pool, toucan_up = _pool_with_flaky_login()

        await _break_one(pool)
        await _break_one(pool)
        assert pool.size == 0

        # Toucan лежит: acquire() поднимает ошибку логина, а не ждёт вечно.
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(pool.acquire().__aenter__(), timeout=3)

        toucan_up["v"] = True
        async with pool.acquire():
            assert pool.size == 1

    asyncio.run(scenario())
