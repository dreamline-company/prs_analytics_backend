"""Транспорт Toucan: сломанный сокет не должен оставлять http.client в REQ_SENT."""

from __future__ import annotations

import http.client
import socket
import threading

import pytest

from shared.integrations.kbrs.api.config import ToucanClientConfig
from shared.integrations.kbrs.api.exceptions import ToucanTransportError
from shared.integrations.kbrs.api.transport import ToucanRpcTransport


def _refused_port() -> int:
    """Порт на loopback, на котором никто не слушает."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_plain_http_client_sticks_in_request_sent_after_connect_error() -> None:
    # Документируем сам баг стандартной библиотеки, ради которого написан _post().
    conn = http.client.HTTPConnection("127.0.0.1", _refused_port(), timeout=2)
    with pytest.raises(ConnectionRefusedError):
        conn.request("POST", "/service", body=b"x")
    with pytest.raises(http.client.CannotSendRequest):
        conn.request("POST", "/service", body=b"x")


def test_connect_error_is_transport_error_and_does_not_poison_connection() -> None:
    transport = ToucanRpcTransport(
        ToucanClientConfig(host="127.0.0.1", port=_refused_port(), timeout_seconds=2),
    )
    for _ in range(2):
        with pytest.raises(ToucanTransportError) as info:
            transport._post(b"x", {}, "method")  # noqa: SLF001
        # Именно ошибка сокета, а не CannotSendRequest с прошлой попытки.
        assert isinstance(info.value.__cause__, ConnectionRefusedError)
        assert not isinstance(info.value.__cause__, http.client.CannotSendRequest)


def test_stale_keepalive_connection_is_retried_once_transparently() -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def serve() -> None:
        # Первое соединение сервер закрывает, не ответив (протухший keep-alive).
        first, _ = srv.accept()
        first.close()
        second, _ = srv.accept()
        second.recv(65536)
        second.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
        )
        second.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    transport = ToucanRpcTransport(
        ToucanClientConfig(host="127.0.0.1", port=port, timeout_seconds=5),
    )
    transport._conn.connect()  # noqa: SLF001 — соединение уже открыто, как при keep-alive

    status, _reason, body = transport._post(b"x", {}, "method")  # noqa: SLF001

    assert (status, body) == (200, b"ok")
