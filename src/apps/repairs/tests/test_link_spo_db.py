"""Связывание СПО на настоящем Postgres: протухание после rollback, дубли
``repairs_spo`` и параллельная запись одного замера.

Включаются переменной ``REPAIRS_TEST_DATABASE_URL`` — async-URL одноразовой
базы (не рабочей!): с миграциями или пустой — недостающие таблицы тест создаст
сам. Без неё пропускаются. Тест параллельной записи коммитит по-настоящему и
удаляет за собой свои строки.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from apps.files.models.file import File
from apps.files.repositories.file import FileRepository
from apps.repairs.models.repair import Repair, RepairType
from apps.repairs.tasks.fetch_sources.candidates import RepairRef
from apps.repairs.tasks.fill_analytics.fetchers.spo_persist import (
    SpoMeasurementPersister,
)
from apps.repairs.tests.test_link_spo_rollback import full_measurement
from apps.wells.models.spo import SPO
from apps.wells.models.spo_event import SPOEvent
from apps.wells.models.well import Well
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.spo_event import SPOEventRepository

DB_URL = os.environ.get("REPAIRS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL,
    reason="REPAIRS_TEST_DATABASE_URL не задан",
)
TABLES = [
    Well.__table__,
    File.__table__,
    SPO.__table__,
    SPOEvent.__table__,
    RepairType.__table__,
    Repair.__table__,
]
# Ключи, которых нет в рабочих данных.
WELL_ABAI_ID = 9_000_000_001
WELL_NAME = "PYTEST_SPO"
MEASURE_ID = 2_000_000_001
SNAPSHOT = datetime(2026, 11, 1, 8, 24, 49)  # noqa: DTZ001


async def _engine() -> AsyncEngine:
    engine = create_async_engine(DB_URL or "")
    async with engine.begin() as conn:
        # Настоящая wells_coord требует PostGIS; скважине нужен лишь ключ для FK.
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS wells_coord "
                "(id BIGSERIAL PRIMARY KEY, abai_id BIGINT NOT NULL UNIQUE)",
            ),
        )
        await conn.run_sync(
            lambda sync: Well.metadata.create_all(sync, tables=TABLES),
        )
    return engine


@asynccontextmanager
async def _rolled_back_session() -> AsyncIterator[AsyncSession]:
    engine = await _engine()
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                await session.close()
                await outer.rollback()
    finally:
        await engine.dispose()


def _in_tx(scenario: Callable[[AsyncSession], Awaitable[None]]) -> None:
    async def run() -> None:
        async with _rolled_back_session() as session:
            await scenario(session)

    asyncio.run(run())


async def _add_well(session: AsyncSession) -> int:
    return (
        await session.execute(
            insert(Well)
            .values(abai_id=WELL_ABAI_ID, name=WELL_NAME)
            .returning(Well.id),
        )
    ).scalar_one()


async def _add_spo(session: AsyncSession, well_id: int) -> int:
    file_id = (
        await session.execute(
            insert(File).values(file="pytest/raw.bin").returning(File.id),
        )
    ).scalar_one()
    return (
        await session.execute(
            insert(SPO)
            .values(
                file_id=file_id,
                snapshot_time=SNAPSHOT,
                well_id=well_id,
                kbrs_measure_id=MEASURE_ID,
                raw_size=3,
            )
            .returning(SPO.id),
        )
    ).scalar_one()


def test_rollback_expires_repair_but_not_its_snapshot() -> None:
    async def scenario(session: AsyncSession) -> None:
        await _add_well(session)
        await session.execute(insert(RepairType).values(abai_id=9_001, name_ru="т"))
        await session.execute(
            insert(Repair).values(
                abai_id=9_000_000_002,
                abai_well_id=WELL_ABAI_ID,
                repair_type_id=9_001,
                start_time=SNAPSHOT,
            ),
        )
        repair = (
            await session.execute(
                select(Repair).where(Repair.abai_id == 9_000_000_002),
            )
        ).scalar_one()
        ref = RepairRef.of(repair)

        await session.rollback()

        with pytest.raises(MissingGreenlet):
            _ = repair.abai_well_id
        assert (ref.abai_well_id, ref.start_time) == (WELL_ABAI_ID, SNAPSHOT)

    _in_tx(scenario)


def test_lookups_pick_earliest_of_duplicate_spos() -> None:
    async def scenario(session: AsyncSession) -> None:
        well_id = await _add_well(session)
        first = await _add_spo(session, well_id)
        await _add_spo(session, well_id)
        repo = SPORepository(session)

        by_measure = await repo.get_by_kbrs_measure_id(MEASURE_ID, well_id=well_id)
        by_snapshot = await repo.get_by_well_id_and_snapshot_time(
            well_id=well_id,
            snapshot_time=SNAPSHOT,
        )

        assert by_measure is not None
        assert by_snapshot is not None
        assert (by_measure.id, by_snapshot.id) == (first, first)

    _in_tx(scenario)


class _SlowStorage:
    """S3, в который первый писатель пишет медленно — окно гонки открыто."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def upload_file(self, _buf: BytesIO, _key: str) -> None:
        await asyncio.sleep(self.delay)


async def _persist(engine: AsyncEngine, well_id: int, *, delay: float) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        spo_repo = SPORepository(session)
        persister = SpoMeasurementPersister(
            storage=_SlowStorage(delay),  # type: ignore[arg-type]
            file_repo=FileRepository(session),
            spo_repo=spo_repo,
            spo_event_repo=SPOEventRepository(session),
        )
        # Как линкер: своё «нет СПО» читается до записи.
        existing = await spo_repo.get_by_kbrs_measure_id(MEASURE_ID, well_id=well_id)
        spo = await persister.persist(
            well_id=well_id,
            measure_id=MEASURE_ID,
            raw_bytes=b"raw",
            full=full_measurement(SNAPSHOT),
            fallback_snapshot_time=SNAPSHOT,
            existing=existing,
        )
        await session.commit()
        assert spo is not None
        return spo.id


async def _cleanup(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        well_ids = select(Well.id).where(Well.abai_id == WELL_ABAI_ID)
        spo_ids = select(SPO.id).where(SPO.well_id.in_(well_ids))
        await conn.execute(delete(SPOEvent).where(SPOEvent.spo_id.in_(spo_ids)))
        await conn.execute(delete(SPO).where(SPO.well_id.in_(well_ids)))
        await conn.execute(delete(File).where(File.file.like("spo/%/2000000001/%")))
        await conn.execute(delete(Well).where(Well.abai_id == WELL_ABAI_ID))


def test_concurrent_persist_of_one_measure_creates_one_spo() -> None:
    async def run() -> None:
        engine = await _engine()
        try:
            await _cleanup(engine)
            async with engine.begin() as conn:
                well_id = (
                    await conn.execute(
                        insert(Well)
                        .values(abai_id=WELL_ABAI_ID, name=WELL_NAME)
                        .returning(Well.id),
                    )
                ).scalar_one()

            slow = asyncio.create_task(_persist(engine, well_id, delay=0.3))
            await asyncio.sleep(0.1)  # второй стартует, пока первый пишет файлы
            fast = asyncio.create_task(_persist(engine, well_id, delay=0))
            ids = await asyncio.gather(slow, fast)

            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        select(func.count())
                        .select_from(SPO)
                        .where(SPO.kbrs_measure_id == MEASURE_ID),
                    )
                ).scalar_one()
            assert count == 1
            assert ids[0] == ids[1]
        finally:
            await _cleanup(engine)
            await engine.dispose()

    asyncio.run(run())
