"""Журнал на настоящем Postgres: SQL эмиттера, отметок, снимка и истории.

Включаются переменной ``NOTIFICATIONS_TEST_DATABASE_URL`` — async-URL базы с
применёнными миграциями (одноразовой, не рабочей!). Без неё пропускаются.

Почти все тесты идут в транзакции с откатом. Тест коммита не по порядку id
коммитит по-настоящему и удаляет за собой строки с префиксом ``pytest:``.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.notifications.constants import local_now
from apps.notifications.dto.internal.notification import NotificationCreateDTO
from apps.notifications.dto.queries.notification import NotificationFilterDTO
from apps.notifications.models.notification import Notification, NotificationView
from apps.notifications.repositories.notification import (
    NotificationRepository,
    NotificationViewRepository,
)
from apps.notifications.services.emitter import NotificationEmitter
from apps.notifications.services.hub import HubClient, NotificationHub, SessionHubStore
from apps.notifications.tests.fakes import FakeWebSocket

DB_URL = os.environ.get("NOTIFICATIONS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL,
    reason="NOTIFICATIONS_TEST_DATABASE_URL не задан",
)
PREFIX = "pytest:"


@asynccontextmanager
async def _rolled_back_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(DB_URL or "")
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


def _dto(key: str, **overrides: object) -> NotificationCreateDTO:
    data: dict[str, object] = {
        "event_code": "system.test",
        "severity": "alarm",
        "title_ru": key,
        "dedup_key": PREFIX + key,
        "event_at": local_now(),
        "abai_ngdu_id": 12,
    }
    data.update(overrides)
    return NotificationCreateDTO(**data)  # type: ignore[arg-type]


EMITTER = NotificationEmitter(enabled=True)


def test_emit_twice_same_dedup_key_is_one_row() -> None:
    async def scenario(session: AsyncSession) -> None:
        first = await EMITTER.emit(session, _dto("dup"))
        second = await EMITTER.emit(session, _dto("dup", title_ru="повтор"))
        assert first is not None
        assert second is None
        rows = (
            await session.execute(
                select(Notification.title_ru).where(
                    Notification.dedup_key == PREFIX + "dup",
                ),
            )
        ).scalars()
        assert list(rows) == ["dup"]

    _in_tx(scenario)


def test_view_all_does_not_touch_later_rows() -> None:
    async def scenario(session: AsyncSession) -> None:
        a = await EMITTER.emit(session, _dto("a"))
        b = await EMITTER.emit(session, _dto("b", severity="info"))
        assert a is not None
        assert b is not None
        # Пришло до нажатия, но id больше up_to_id: экран его ещё не видел.
        later = await EMITTER.emit(session, _dto("later", severity="warning"))
        assert later is not None
        assert later > b
        views = NotificationViewRepository(session)
        marked = await views.mark_up_to(b, local_now())
        assert {r.notification_id for r in marked} >= {a, b}
        assert later not in {r.notification_id for r in marked}
        # Повторное «всё просмотрено» ничего не отмечает заново.
        assert await views.mark_up_to(b, local_now()) == []
        assert await views.mark_one(a, local_now()) == []
        repo = NotificationRepository(session)
        unviewed = await repo.list_rows(NotificationFilterDTO(viewed=False, limit=200))
        assert later in {r.id for r in unviewed}
        assert not {a, b} & {r.id for r in unviewed}
        counts = await repo.count_unviewed_by_severity()
        assert counts.get("warning", 0) >= 1

    _in_tx(scenario)


def test_failed_emit_keeps_producer_transaction() -> None:
    """Несуществующая скважина: уведомления нет, транзакция жива."""

    async def scenario(session: AsyncSession) -> None:
        assert await EMITTER.emit(session, _dto("bad-well", well_id=2**62)) is None
        assert await EMITTER.emit(session, _dto("after-bad")) is not None

    _in_tx(scenario)


def test_history_filters_chain_and_snapshot_window() -> None:
    async def scenario(session: AsyncSession) -> None:
        repo = NotificationRepository(session)
        now = local_now()
        old = await EMITTER.emit(
            session,
            _dto("old", source_type="pytest", source_id=1, event_at=now),
        )
        new = await EMITTER.emit(
            session,
            _dto("new", source_type="pytest", source_id=1, severity="info"),
        )
        assert old is not None
        assert new is not None
        alarms = await repo.list_rows(
            NotificationFilterDTO(
                severities=["alarm"],
                viewed=False,
                abai_ngdu_id=12,
                event_codes=["system.test"],
                limit=200,
            ),
        )
        assert old in {r.id for r in alarms}
        assert new not in {r.id for r in alarms}
        chain = await repo.list_chain("pytest", 1)
        assert [r.id for r in chain] == [old, new]

        # Снимок по after_id=new: строки «после» нет, но окно 15 мин отдаёт и
        # old (у него id меньше — как у закоммиченной не по порядку).
        snap = await repo.list_snapshot_rows(
            after_id=new,
            since=now - timedelta(minutes=15),
            latest=50,
            limit=200,
        )
        assert {old, new} <= {r.id for r in snap}
        # Вне окна по after_id приходит только то, что после него.
        late = await repo.list_snapshot_rows(
            after_id=old,
            since=now + timedelta(hours=1),
            latest=50,
            limit=200,
        )
        assert [r.id for r in late] == [new]

    _in_tx(scenario)


def test_hub_delivers_row_committed_out_of_id_order() -> None:
    """Транзакция A берёт id раньше B, а коммитится позже — хаб доставит оба."""

    async def scenario() -> None:
        engine = create_async_engine(DB_URL or "")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        hub = NotificationHub(SessionHubStore(maker), None)
        ws = FakeWebSocket()
        client = HubClient(ws)
        writer = asyncio.create_task(client.run())
        try:
            await hub.attach(client, None)
            async with maker() as tx_a, maker() as tx_b:
                id_a = await EMITTER.emit(tx_a, _dto("order-a"))
                id_b = await EMITTER.emit(tx_b, _dto("order-b"))
                assert id_a is not None
                assert id_b is not None
                assert id_a < id_b
                await tx_b.commit()
                await hub.poll_once()
                await tx_a.commit()
            await hub.poll_once()
            for _ in range(20):
                await asyncio.sleep(0)
            delivered = [i for i in ws.notification_ids() if i in {id_a, id_b}]
            assert delivered == [id_b, id_a]
        finally:
            writer.cancel()
            async with maker() as cleanup:
                ids = select(Notification.id).where(
                    Notification.dedup_key.startswith(PREFIX),
                )
                await cleanup.execute(
                    delete(NotificationView).where(
                        NotificationView.notification_id.in_(ids),
                    ),
                )
                await cleanup.execute(
                    delete(Notification).where(
                        Notification.dedup_key.startswith(PREFIX),
                    ),
                )
                await cleanup.commit()
            await engine.dispose()

    asyncio.run(scenario())


def test_local_now_is_naive() -> None:
    assert isinstance(local_now(), datetime)
    assert local_now().tzinfo is None
