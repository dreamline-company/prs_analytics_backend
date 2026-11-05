"""Нарушения ТБ в дашборде — только обработанные экраны CM."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.settings import get_settings
from shared.integrations.cm.models import BrigadeErrorScreen
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)

START = datetime(2026, 10, 30)  # noqa: DTZ001 — наивное местное время, как в БД
END = datetime(2026, 11, 5)  # noqa: DTZ001
TZ = get_settings().ZONE_INFO


def _screen(
    screen_id: int,
    brigade_id: int,
    day: int,
    *,
    processed: bool,
) -> BrigadeErrorScreen:
    return BrigadeErrorScreen(
        id=screen_id,
        brigade_id=brigade_id,
        screen=f"screens/{screen_id}.png",
        description="Работник без каски",
        is_processed=processed,
        timestamp=datetime(2026, 11, day, 12, 0, tzinfo=TZ).astimezone(UTC),
    )


async def _query(**kwargs: bool) -> list[int]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BrigadeErrorScreen.__table__.create)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add_all(
            [
                _screen(1, 34, 1, processed=True),
                _screen(2, 34, 2, processed=False),
                _screen(3, 34, 3, processed=True),
                _screen(4, 99, 3, processed=True),  # чужая бригада
                _screen(5, 34, 4, processed=False),
            ],
        )
        await session.commit()
        rows = await CMBrigadeErrorScreenRepository(
            session,
        ).list_by_brigade_ids_in_range(
            [34],
            start_time=START,
            end_time=END,
            **kwargs,
        )
    await engine.dispose()
    return [r.id for r in rows]


def test_only_processed_screens_by_default() -> None:
    assert asyncio.run(_query()) == [1, 3]


def test_all_screens_when_requested() -> None:
    assert asyncio.run(_query(only_processed=False)) == [1, 2, 3, 5]
