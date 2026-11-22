"""Хаб: доставка без потерь при сбоях Redis и коммитах не по порядку id."""

import asyncio
from collections.abc import Awaitable, Callable

from apps.notifications.services.hub import HubClient, NotificationHub
from apps.notifications.tests.fakes import (
    NOW,
    BrokenSource,
    FakeStore,
    FakeWebSocket,
    QueueSource,
    item,
    minutes,
)


def _hub(store: FakeStore, source: object = None, **kwargs: object) -> NotificationHub:
    clock = kwargs.pop("clock", lambda: store.now)
    return NotificationHub(store, source, clock=clock, **kwargs)  # type: ignore[arg-type]


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def _connect(
    hub: NotificationHub,
    after_id: int | None = None,
    ws: FakeWebSocket | None = None,
) -> tuple[FakeWebSocket, HubClient, asyncio.Task[None]]:
    ws = ws or FakeWebSocket()
    client = HubClient(ws)
    writer = asyncio.create_task(client.run())
    await hub.attach(client, after_id)
    await _settle()
    return ws, client, writer


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def test_snapshot_first_then_live_notification() -> None:
    async def scenario() -> None:
        store = FakeStore()
        store.commit(item(1, created_at=NOW - minutes(60)))
        hub = _hub(store)
        ws, _, _ = await _connect(hub)
        store.commit(item(2))
        await hub.handle_message({"notification_ids": [2]})
        await _settle()
        assert ws.types() == ["snapshot", "notification"]
        assert [i["id"] for i in ws.frames[0]["items"]] == [1]
        assert ws.frames[0]["unviewed"] == {"alarm": 1, "warning": 0, "info": 0}
        assert ws.notification_ids() == [2]

    _run(scenario)


def test_out_of_order_commit_still_delivered() -> None:
    """id 10 закоммичен после id 11: курсор «> 11» его бы потерял, опрос — нет."""

    async def scenario() -> None:
        store = FakeStore()
        hub = _hub(store)
        ws, _, _ = await _connect(hub)

        store.commit(item(11))
        await hub.handle_message({"notification_ids": [11]})
        # Сигнал о 10 пропал (Redis моргнул), строка видна позже.
        store.commit(item(10))
        await hub.poll_once()
        await hub.poll_once()
        await _settle()

        assert ws.notification_ids() == [11, 10]

    _run(scenario)


def test_signal_before_row_is_visible_is_retried_by_poll() -> None:
    async def scenario() -> None:
        store = FakeStore()
        hub = _hub(store)
        ws, _, _ = await _connect(hub)
        await hub.handle_message({"notification_ids": [5]})  # строки ещё не видно
        store.commit(item(5))
        await hub.poll_once()
        await _settle()
        assert ws.notification_ids() == [5]

    _run(scenario)


def test_reconnect_with_after_id_gets_missed_and_window() -> None:
    async def scenario() -> None:
        store = FakeStore()
        # Старое и уже виденное — не повторяется; пропущенное после 7 — приходит;
        # 6 закоммичен поздно (в окне 15 мин) — тоже приходит.
        store.commit(
            item(3, created_at=NOW - minutes(120)),
            item(6, created_at=NOW - minutes(5)),
            item(7, created_at=NOW - minutes(60)),
            item(8, created_at=NOW - minutes(40)),
            item(9, created_at=NOW - minutes(30)),
        )
        hub = _hub(store)
        ws, _, _ = await _connect(hub, after_id=7)
        assert "snapshot:7" in store.calls
        snapshot = ws.frames[0]
        assert snapshot["type"] == "snapshot"
        assert [i["id"] for i in snapshot["items"]] == [9, 8, 6]

        # Снимок засеял отправленное: опрос не повторяет 6 кадром notification.
        await hub.poll_once()
        await _settle()
        assert ws.notification_ids() == []

    _run(scenario)


def test_redis_down_delivery_by_poll_loop() -> None:
    async def scenario() -> None:
        store = FakeStore()
        source = BrokenSource()
        hub = _hub(store, source, poll_interval=0.01, redis_retry_max=0.01)
        await hub.start()
        try:
            ws, _, _ = await _connect(hub)
            store.commit(item(1))
            for _ in range(100):
                if ws.notification_ids():
                    break
                await asyncio.sleep(0.01)
            assert ws.notification_ids() == [1]
            assert source.attempts >= 1
        finally:
            await hub.stop()

    _run(scenario)


def test_redis_signal_delivers_without_waiting_for_poll() -> None:
    async def scenario() -> None:
        store = FakeStore()
        source = QueueSource()
        hub = _hub(store, source, poll_interval=3600)
        await hub.start()
        try:
            ws, _, _ = await _connect(hub)
            store.commit(item(4))
            source.queue.put_nowait({"notification_ids": [4]})
            for _ in range(100):
                if ws.notification_ids():
                    break
                await asyncio.sleep(0.01)
            assert ws.notification_ids() == [4]
        finally:
            await hub.stop()

    _run(scenario)


def test_failed_socket_does_not_break_others() -> None:
    async def scenario() -> None:
        store = FakeStore()
        hub = _hub(store)
        good, _, _ = await _connect(hub)
        bad_ws = FakeWebSocket()
        _, bad_client, _ = await _connect(hub, ws=bad_ws)
        bad_ws.fail = True
        store.commit(item(1), item(2))
        await hub.handle_message({"notification_ids": [1]})
        await _settle()
        await hub.handle_message({"notification_ids": [2]})
        await _settle()
        assert good.notification_ids() == [1, 2]
        assert bad_client.closed
        assert bad_ws.closed_with == 1013
        assert hub.client_count == 1

    _run(scenario)


def test_no_clients_no_poll_and_signals_ignored() -> None:
    async def scenario() -> None:
        store = FakeStore()
        store.commit(item(1))
        hub = _hub(store)
        await hub.poll_once()
        await hub.handle_message({"notification_ids": [1]})
        assert store.calls == []

    _run(scenario)


def test_viewed_frame_grouped_by_time_with_counts() -> None:
    async def scenario() -> None:
        store = FakeStore()
        store.commit(item(1), item(2), item(3, severity="info"))
        hub = _hub(store)
        ws, _, _ = await _connect(hub)
        store.view(100, 1, NOW)
        store.view(101, 2, NOW)
        await hub.handle_message({"view_ids": [100, 101]})
        # Повтор того же сигнала и опрос не дублируют кадр.
        await hub.handle_message({"view_ids": [100, 101]})
        await hub.poll_once()
        await _settle()
        viewed = [f for f in ws.frames if f["type"] == "viewed"]
        assert len(viewed) == 1
        assert viewed[0]["ids"] == [1, 2]
        assert viewed[0]["viewed_at"] == NOW.isoformat()
        assert viewed[0]["unviewed"] == {"alarm": 0, "warning": 0, "info": 1}

    _run(scenario)


def test_lost_view_signal_delivered_by_poll() -> None:
    async def scenario() -> None:
        store = FakeStore()
        store.commit(item(1))
        hub = _hub(store)
        ws, _, _ = await _connect(hub)
        store.view(7, 1, NOW)
        await hub.poll_once()
        await _settle()
        assert [f["ids"] for f in ws.frames if f["type"] == "viewed"] == [[1]]

    _run(scenario)


def test_ping_keeps_last_counts_when_db_fails() -> None:
    async def scenario() -> None:
        store = FakeStore()
        store.commit(item(1, severity="warning"))
        hub = _hub(store)
        ws, _, _ = await _connect(hub)
        store.fail_unviewed = True
        await hub.ping_once()
        await _settle()
        assert ws.frames[-1] == {
            "type": "ping",
            "unviewed": {"alarm": 0, "warning": 1, "info": 0},
        }

    _run(scenario)


def test_second_hello_sends_new_snapshot() -> None:
    async def scenario() -> None:
        store = FakeStore()
        hub = _hub(store)
        ws, client, _ = await _connect(hub)
        store.commit(item(1))
        await hub.attach(client, None)
        await _settle()
        assert ws.types() == ["snapshot", "snapshot"]
        assert [i["id"] for i in ws.frames[1]["items"]] == [1]
        assert hub.client_count == 1

    _run(scenario)
