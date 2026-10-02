"""События СПО из заголовка — только на 10-байтовой сетке отсчётов.

Побайтовый перебор заголовка находил «время + канал 5» в случайных байтах, и
такое событие давало код работы вроде -1419067657 (статус «Работа [...]»).
"""

import struct

from shared.integrations.kbrs.api.parsers import MeasurementDetailsParser

START_TS = 1790668800  # 2026-09-29 08:00
HOOK_WEIGHT = 0x0200
WORK_TYPE = 5


def _measurement() -> bytes:
    """Заголовок 100 байт с событием на сетке (80) и ложным вне её (33)."""
    header = bytearray(100)
    header[0:2] = b"\x55\xaa"
    struct.pack_into(">IHi", header, 80, START_TS - 600, WORK_TYPE, 7)
    struct.pack_into(">IHi", header, 33, START_TS - 300, WORK_TYPE, -1419067657)
    points = b"".join(
        struct.pack(">IHi", START_TS + i, HOOK_WEIGHT, 20_000 + i) for i in range(12)
    )
    return bytes(header) + points


def test_header_event_off_record_grid_is_dropped() -> None:
    details = MeasurementDetailsParser.parse(_measurement())

    assert details.sample_offset == 100
    assert [(e.offset, e.code) for e in details.events] == [(80, 7)]
