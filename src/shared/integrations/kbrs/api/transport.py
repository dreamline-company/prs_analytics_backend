from __future__ import annotations

import http.client
from typing import Iterable, Optional

from .codec import TlvCodec, ToucanFrameCodec
from .config import ToucanClientConfig
from .dtos import RpcResponseDto
from .exceptions import ToucanTransportError


# Соединение умерло до получения ответа: RemoteDisconnected (FIN) или сброс
# сокета (RST) на отправке/чтении. Сюда же попадает http.client.RemoteDisconnected,
# так как он наследует ConnectionResetError.
_STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    ConnectionResetError,
    BrokenPipeError,
)


class ToucanRpcTransport:
    """HTTP /service transport for Toucan binary RPC calls."""

    def __init__(self, config: ToucanClientConfig):
        self.config = config
        self.session_id = ""
        self._conn = http.client.HTTPConnection(
            config.host, config.port, timeout=config.timeout_seconds
        )

    def close(self) -> None:
        self._conn.close()

    def set_session_id(self, session_id: str) -> None:
        self.session_id = session_id

    def call_raw(
        self, method: str, params: Iterable[bytes], *, sid: Optional[str] = None
    ) -> bytes:
        payload = TlvCodec.build_rpc_payload(
            method, params, self.session_id if sid is None else sid
        )
        body = ToucanFrameCodec.build_http_body(payload)
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/binary",
            "User-Agent": self.config.user_agent,
            "Connection": "Keep-Alive" if self.config.keep_alive else "close",
        }
        resp_status, resp_reason, resp_body = self._post(body, headers, method)
        if resp_status != 200:
            raise ToucanTransportError(
                f"HTTP {resp_status} {resp_reason}: {resp_body[:200]!r}"
            )
        decoded, _zoff = ToucanFrameCodec.find_and_decompress_zlib(resp_body)
        return decoded

    def _post(
        self, body: bytes, headers: dict[str, str], method: str
    ) -> tuple[int, str, bytes]:
        """Один HTTP POST с корректной обработкой сломанного соединения.

        ``http.client`` при ошибке на отправке (нет маршрута, таймаут connect)
        остаётся в состоянии ``Request-sent`` и дальше на каждый вызов бросает
        ``CannotSendRequest`` — поэтому любую ошибку сокета/протокола
        закрываем ``close()`` (сбрасывает состояние) и поднимаем
        ``ToucanTransportError``, чтобы пул пересоздал клиент.

        Обрыв до единого байта ответа (``RemoteDisconnected`` при FIN или
        ``ConnectionResetError``/``BrokenPipeError`` при RST) — штатное закрытие
        простаивающего keep-alive соединения сервером: один прозрачный повтор
        на новом соединении, сессия Toucan при этом не теряется.
        """
        for attempt in (1, 2):
            try:
                self._conn.request(
                    "POST", self.config.http_path, body=body, headers=headers
                )
                resp = self._conn.getresponse()
                return resp.status, resp.reason, resp.read()
            except _STALE_CONNECTION_ERRORS as exc:
                self._conn.close()
                if attempt == 1:
                    continue
                raise ToucanTransportError(
                    f"{method}: server closed the connection without response: {exc!r}",
                ) from exc
            except (OSError, http.client.HTTPException) as exc:
                self._conn.close()
                raise ToucanTransportError(
                    f"{method}: transport failure: {exc!r}"
                ) from exc
        raise AssertionError("unreachable")

    def call_rpc(
        self, method: str, params: Iterable[bytes], *, sid: Optional[str] = None
    ) -> RpcResponseDto:
        decoded = self.call_raw(method, params, sid=sid)
        response = TlvCodec.parse_rpc_response(decoded)
        if response.method != method:
            raise ToucanTransportError(
                f"unexpected RPC method echo: expected {method}, got {response.method}"
            )
        return response
