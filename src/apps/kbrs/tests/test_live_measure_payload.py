"""Замер, который ещё пишется: Toucan отдаёт его в RPC-обёртке, а не голым 0x55AA."""

import struct

import pytest

from shared.integrations.kbrs.api.codec import TlvCodec
from shared.integrations.kbrs.api.constants import TERMINATOR, TYPE_OBJECT
from shared.integrations.kbrs.api.exceptions import ToucanDecodeError
from shared.integrations.kbrs.api.parsers import (
    MeasurementFullParser,
    MeasurementPassportPeeker,
)

START_TS = 1790668800  # 2026-09-29 08:00
HOOK_WEIGHT = 0x0200


def _measurement(well: bytes = b"94", samples: int = 12) -> bytes:
    """Блок 0x55AA: заголовок ДЭЛ-150 с номером скважины + точки веса на крюке."""
    header = bytearray(0x60)
    header[0:2] = b"\x55\xaa"
    header[0x49 : 0x49 + len(well)] = well
    points = b"".join(
        struct.pack(">IHi", START_TS + i, HOOK_WEIGHT, 20_000 + i)
        for i in range(samples)
    )
    return bytes(header) + points


def _live(inner: bytes) -> bytes:
    """Ответ TNOMeasureLoadView на пишущийся замер, как его шлёт Toucan."""
    data_compressed = (
        struct.pack("<I", 0x80000000)  # длина с флагом в старшем бите
        + bytes([TYPE_OBJECT])
        + struct.pack("<i", len(b"DataCompressed"))
        + b"DataCompressed"
    )
    measure = (
        TlvCodec.int32("MeasureID", 251480)
        + TlvCodec.enum("DataType", "mdtBinary")
        + TlvCodec.field(
            "DataBinary",
            TYPE_OBJECT,
            struct.pack("<i", len(inner)) + inner,
        )
        + data_compressed
        + TERMINATOR
    )
    return (
        TlvCodec.int32("RID", 0)
        + TlvCodec.enum("ErrorType", "petNone")
        + TlvCodec.wstr("Error", "")
        + TERMINATOR
        + TlvCodec.raw_wstr("TNOMeasureLoadView")
        + TlvCodec.enum("Direction", "mlvtNone")
        + TlvCodec.field("Measure", TYPE_OBJECT, measure)
        + TlvCodec.wstr("ViewConfiguration", "[FMSettings]\r\nVertical=false")
        + TERMINATOR
    )


def test_live_measure_parses_like_finished_one() -> None:
    inner = _measurement()

    live = MeasurementFullParser.parse(_live(inner))
    finished = MeasurementFullParser.parse(inner)

    assert live.chart.rows == finished.chart.rows
    assert len(live.chart.rows) == 12
    assert live.details.passport.well == 94


def test_well_is_read_from_live_measure() -> None:
    assert MeasurementPassportPeeker.read_well(_live(_measurement(b"2791"))) == 2791


def test_not_a_measure_is_still_a_decode_error() -> None:
    assert MeasurementPassportPeeker.read_well(b"garbage" * 10) is None
    with pytest.raises(ToucanDecodeError):
        MeasurementFullParser.parse(b"garbage" * 10)
