from __future__ import annotations

import datetime as dt

MAGIC_SESSION = 0x004A99FA
DEFAULT_TOUCAN_PORT = 17997
DEFAULT_VERSION = "1.12.2184"

TYPE_INT32 = 0x01
TYPE_ENUM = 0x03
TYPE_DOUBLE = 0x04
TYPE_OBJECT = 0x07
TYPE_WSTR = 0x12
TERMINATOR = b"\x00\x00\x00\x00"
DELPHI_BASE = dt.datetime(1899, 12, 30)

# Channel identifiers observed in the pcap for DEL-150 measurement graphs.
# value = (public DTO field, scale divisor, clamp negative values to zero)
CHANNEL_MAP: dict[int, tuple[str, float, bool]] = {
    0x0200: ("hook_weight_t", 1000.0, True),
    0x137E: ("h2s_mg_m3", 1000.0, False),
    0x1397: ("ch4_percent", 1000.0, False),
}
