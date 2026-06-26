from __future__ import annotations

import datetime as dt
import random
import struct
import time
import zlib
from typing import Iterable, Optional

from .constants import (
    DELPHI_BASE,
    MAGIC_SESSION,
    TERMINATOR,
    TYPE_DOUBLE,
    TYPE_ENUM,
    TYPE_INT32,
    TYPE_WSTR,
)
from .dtos import RpcFieldDto, RpcResponseDto
from .exceptions import ToucanProtocolError


class Crc16Modbus:
    @staticmethod
    def calculate(data: bytes) -> int:
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
                crc &= 0xFFFF
        return crc


class DelphiDateTimeCodec:
    @staticmethod
    def to_delphi(value: dt.datetime) -> float:
        delta = value - DELPHI_BASE
        return delta.days + (delta.seconds + delta.microseconds / 1_000_000) / 86400

    @staticmethod
    def from_delphi(value: float) -> dt.datetime:
        return DELPHI_BASE + dt.timedelta(days=value)


class TlvCodec:
    @staticmethod
    def raw_wstr(value: str) -> bytes:
        return struct.pack("<i", len(value)) + value.encode("utf-16le")

    @staticmethod
    def read_raw_wstr(buf: bytes, off: int) -> tuple[str, int]:
        if off + 4 > len(buf):
            raise ToucanProtocolError("not enough bytes for WideString length")
        n = struct.unpack_from("<i", buf, off)[0]
        if n < 0 or off + 4 + n * 2 > len(buf):
            raise ToucanProtocolError(f"invalid WideString length {n} at offset {off}")
        start = off + 4
        end = start + n * 2
        return buf[start:end].decode("utf-16le", errors="replace"), end

    @staticmethod
    def field(name: str, field_type: int, value: bytes) -> bytes:
        name_b = name.encode("latin1")
        return (
            struct.pack("<i", len(value))
            + struct.pack("<B", field_type)
            + struct.pack("<i", len(name_b))
            + name_b
            + value
        )

    @classmethod
    def int32(cls, name: str, value: int) -> bytes:
        return cls.field(name, TYPE_INT32, struct.pack("<i", int(value)))

    @classmethod
    def double(cls, name: str, value: float) -> bytes:
        return cls.field(name, TYPE_DOUBLE, struct.pack("<d", float(value)))

    @classmethod
    def wstr(cls, name: str, value: str) -> bytes:
        return cls.field(name, TYPE_WSTR, cls.raw_wstr(value))

    @classmethod
    def enum(cls, name: str, value: str | bool) -> bytes:
        if isinstance(value, bool):
            value = "true" if value else "false"
        return cls.field(name, TYPE_ENUM, cls.raw_wstr(str(value)))

    @classmethod
    def build_rpc_payload(cls, method: str, params: Iterable[bytes], sid: str = "", rid: int = 0) -> bytes:
        return cls.int32("RID", rid) + cls.wstr("SID", sid) + TERMINATOR + cls.raw_wstr(method) + b"".join(params) + TERMINATOR

    @classmethod
    def parse_one_field(cls, buf: bytes, off: int) -> tuple[Optional[RpcFieldDto], int]:
        if off + 4 > len(buf):
            raise ToucanProtocolError("truncated field size")
        size = struct.unpack_from("<i", buf, off)[0]
        if size == 0:
            return None, off + 4
        if size < 0 or off + 9 > len(buf):
            raise ToucanProtocolError(f"invalid field at offset {off}")
        field_type = buf[off + 4]
        name_len = struct.unpack_from("<i", buf, off + 5)[0]
        name_start = off + 9
        name_end = name_start + name_len
        value_start = name_end
        value_end = value_start + size
        if name_len < 0 or value_end > len(buf):
            raise ToucanProtocolError(f"truncated field value at offset {off}")
        name = buf[name_start:name_end].decode("latin1", errors="replace")
        raw = buf[value_start:value_end]
        value: object = raw
        try:
            if field_type == TYPE_INT32 and len(raw) >= 4:
                value = struct.unpack_from("<i", raw, 0)[0]
            elif field_type == TYPE_DOUBLE and len(raw) >= 8:
                value = struct.unpack_from("<d", raw, 0)[0]
            elif field_type in (TYPE_ENUM, TYPE_WSTR):
                value, _ = cls.read_raw_wstr(raw, 0)
        except Exception:
            value = raw
        return RpcFieldDto(name=name, type_code=field_type, raw=raw, value=value, offset=off, end=value_end), value_end

    @classmethod
    def parse_field_list(cls, buf: bytes, off: int = 0) -> tuple[list[RpcFieldDto], int]:
        fields: list[RpcFieldDto] = []
        while off < len(buf):
            field, off = cls.parse_one_field(buf, off)
            if field is None:
                break
            fields.append(field)
        return fields, off

    @classmethod
    def parse_rpc_response(cls, buf: bytes) -> RpcResponseDto:
        header_fields, off = cls.parse_field_list(buf, 0)
        method, off = cls.read_raw_wstr(buf, off)
        result_fields, _ = cls.parse_field_list(buf, off)
        header = {f.name: f.value for f in header_fields}
        result = {f.name: f for f in result_fields}
        err_type = header.get("ErrorType")
        err = header.get("Error")
        if err_type and err_type != "petNone":
            raise ToucanProtocolError(f"Toucan returned {err_type}: {err!r}")
        return RpcResponseDto(header=header, method=method, result=result)


class ToucanFrameCodec:
    @staticmethod
    def build_http_body(payload: bytes) -> bytes:
        compressed = zlib.compress(payload)
        frame_len = 26 + len(compressed)
        ticks = time.monotonic_ns() & 0xFFFFFFFF
        checksum = Crc16Modbus.calculate(compressed)
        tail2 = random.randint(0, 0xFFFF)
        return struct.pack("<IIIIIIH", frame_len, ticks, 26, checksum, MAGIC_SESSION, 1, tail2) + compressed

    @staticmethod
    def find_and_decompress_zlib(body: bytes) -> tuple[bytes, int]:
        for off in range(0, min(96, len(body) - 2)):
            if body[off] == 0x78 and body[off + 1] in (0x01, 0x5E, 0x9C, 0xDA):
                try:
                    return zlib.decompress(body[off:]), off
                except zlib.error:
                    pass
        raise ToucanProtocolError("zlib stream was not found in Toucan response body")
