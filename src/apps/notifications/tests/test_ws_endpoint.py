"""WS-эндпоинт: hello → snapshot, мусорный кадр → 1007, прочее игнорируется."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.notifications.routers.ws import ws_router
from apps.notifications.routers.ws.v1.notifications import (
    BadNotificationFrameError,
    parse_client_frame,
)
from apps.notifications.services.hub import NotificationHub
from apps.notifications.tests.fakes import NOW, FakeStore, item


def _client(store: FakeStore) -> tuple[TestClient, NotificationHub]:
    app = FastAPI()
    app.include_router(ws_router, prefix="/prs-analytics/ws")
    hub = NotificationHub(store, None, clock=lambda: NOW)  # type: ignore[arg-type]
    app.state.notification_hub = hub
    return TestClient(app), hub


URL = "/prs-analytics/ws/notifications/v1"


def test_hello_returns_snapshot_and_other_frames_are_ignored() -> None:
    store = FakeStore()
    store.commit(item(1), item(2, severity="info"))
    client, hub = _client(store)
    with client.websocket_connect(URL) as ws:
        ws.send_json({"type": "pong"})
        ws.send_json({"type": "hello", "after_id": None})
        frame = ws.receive_json()
        assert frame["type"] == "snapshot"
        assert [i["id"] for i in frame["items"]] == [2, 1]
        assert frame["unviewed"] == {"alarm": 1, "warning": 0, "info": 1}
        assert hub.client_count == 1
    assert hub.client_count == 0


def test_bad_json_closes_with_1007() -> None:
    client, _ = _client(FakeStore())
    with client.websocket_connect(URL) as ws:
        ws.send_text("not json")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1007


def test_parse_client_frame() -> None:
    assert parse_client_frame('{"type":"hello","after_id":5}').after_id == 5
    assert parse_client_frame('{"type":"hello"}').after_id is None
    assert parse_client_frame('{"type":"pong"}') is None
    # Валидный JSON, но не объект — keepalive, игнорируется.
    for ignored in ("[1]", '"ping"', "123", "null"):
        assert parse_client_frame(ignored) is None
    for bad in (
        '{"type":"hello","after_id":"x"}',
        '{"type":"hello","after_id":"5"}',
        '{"type":"hello","after_id":true}',
        '{"type":"hello","after_id":99999999999999999999999}',
        "{",
        '{"type":"hello","after_id":-1}',
    ):
        with pytest.raises(BadNotificationFrameError):
            parse_client_frame(bad)


class _BrokenSnapshotStore(FakeStore):
    async def snapshot(self, **_: object) -> list:  # type: ignore[override]
        msg = "db down"
        raise RuntimeError(msg)


def test_snapshot_failure_closes_with_1011() -> None:
    client, hub = _client(_BrokenSnapshotStore())
    with client.websocket_connect(URL) as ws:
        ws.send_json({"type": "hello", "after_id": 3})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1011
    assert hub.client_count == 0
