from __future__ import annotations

import http.client
from typing import Iterable, Optional

from .codec import TlvCodec, ToucanFrameCodec
from .config import ToucanClientConfig
from .dtos import RpcResponseDto
from .exceptions import ToucanTransportError


class ToucanRpcTransport:
    """HTTP /service transport for Toucan binary RPC calls."""

    def __init__(self, config: ToucanClientConfig):
        self.config = config
        self.session_id = ""
        self._conn = http.client.HTTPConnection(config.host, config.port, timeout=config.timeout_seconds)

    def close(self) -> None:
        self._conn.close()

    def set_session_id(self, session_id: str) -> None:
        self.session_id = session_id

    def call_raw(self, method: str, params: Iterable[bytes], *, sid: Optional[str] = None) -> bytes:
        payload = TlvCodec.build_rpc_payload(method, params, self.session_id if sid is None else sid)
        body = ToucanFrameCodec.build_http_body(payload)
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/binary",
            "User-Agent": self.config.user_agent,
            "Connection": "Keep-Alive" if self.config.keep_alive else "close",
        }
        self._conn.request("POST", self.config.http_path, body=body, headers=headers)
        resp = self._conn.getresponse()
        resp_body = resp.read()
        if resp.status != 200:
            raise ToucanTransportError(f"HTTP {resp.status} {resp.reason}: {resp_body[:200]!r}")
        decoded, _zoff = ToucanFrameCodec.find_and_decompress_zlib(resp_body)
        return decoded

    def call_rpc(self, method: str, params: Iterable[bytes], *, sid: Optional[str] = None) -> RpcResponseDto:
        decoded = self.call_raw(method, params, sid=sid)
        response = TlvCodec.parse_rpc_response(decoded)
        if response.method != method:
            raise ToucanTransportError(f"unexpected RPC method echo: expected {method}, got {response.method}")
        return response
