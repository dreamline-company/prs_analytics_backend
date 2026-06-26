from __future__ import annotations

import csv
import datetime as dt
import re
import struct
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .codec import TlvCodec
from .constants import CHANNEL_MAP
from .dtos import (
    DeviceDto,
    DirectoryDto,
    MeasureRowDto,
    MeasurementParsedDto,
    MeasurementRecordDto,
    MeasurementRowDto,
    OwnerDto,
)
from .exceptions import ToucanDecodeError


class MidasStringCodec:
    @staticmethod
    def decode_utf16_len1(buf: bytes, pos: int) -> tuple[str, int]:
        if pos >= len(buf):
            raise ToucanDecodeError("string length outside buffer")
        n = buf[pos]
        start = pos + 1
        end = start + n
        if n % 2 or end > len(buf):
            raise ToucanDecodeError("bad 1-byte UTF-16 string")
        return buf[start:end].decode("utf-16le", errors="replace"), end

    @staticmethod
    def decode_utf16_len2(buf: bytes, pos: int) -> tuple[str, int]:
        if pos + 2 > len(buf):
            raise ToucanDecodeError("string length outside buffer")
        n = struct.unpack_from("<H", buf, pos)[0]
        start = pos + 2
        end = start + n
        if n % 2 or end > len(buf):
            raise ToucanDecodeError("bad 2-byte UTF-16 string")
        return buf[start:end].decode("utf-16le", errors="replace"), end


class DirectoryDataParser:
    """Parser for DirectoryData returned by TNOConnect."""

    @classmethod
    def parse(cls, directory_data: bytes) -> DirectoryDto:
        fields, _ = TlvCodec.parse_field_list(directory_data, 0)
        by_name = {f.name: f for f in fields}
        if "OwnersDataset" not in by_name or "DirectoryDataset" not in by_name:
            raise ToucanDecodeError("DirectoryData does not contain OwnersDataset/DirectoryDataset")

        owners = cls.parse_owners_dataset(by_name["OwnersDataset"].raw)
        devices = cls.parse_directory_dataset(by_name["DirectoryDataset"].raw)
        owner_map = {o.owner_id: o for o in owners}
        enriched_devices: list[DeviceDto] = []
        for d in devices:
            owner = owner_map.get(d.owner_id)
            enriched_devices.append(
                DeviceDto(
                    device_id=d.device_id,
                    owner_id=d.owner_id,
                    directory_id=d.directory_id,
                    dirtype=d.dirtype,
                    description=d.description,
                    owner_name=owner.owner_name if owner else "",
                    owner_short_name=owner.owner_short_name if owner else "",
                )
            )
        return DirectoryDto(owners=owners, devices=enriched_devices)

    @staticmethod
    def _looks_like_device_row(buf: bytes, pos: int) -> bool:
        if pos + 18 > len(buf):
            return False
        try:
            _flags, directory_id, owner_id, dirtype = struct.unpack_from("<iiii", buf, pos)
            name, off = MidasStringCodec.decode_utf16_len1(buf, pos + 16)
            desc, _end = MidasStringCodec.decode_utf16_len2(buf, off)
        except Exception:
            return False
        if not (0 <= directory_id <= 1_000_000 and 0 <= owner_id <= 100 and 0 <= dirtype <= 20):
            return False
        if not name or not re.fullmatch(r"[0-9A-Za-z_.-]+", name):
            return False
        if any(ord(ch) > 0x4000 for ch in desc):
            return False
        return True

    @classmethod
    def parse_directory_dataset(cls, raw: bytes) -> list[DeviceDto]:
        start: Optional[int] = None
        for pos in range(64, min(len(raw) - 32, 512)):
            if cls._looks_like_device_row(raw, pos):
                start = pos
                break
        if start is None:
            raise ToucanDecodeError("could not find first DirectoryDataset row")

        devices: list[DeviceDto] = []
        pos = start
        while pos < len(raw) - 1:
            if not cls._looks_like_device_row(raw, pos):
                break
            _flags, directory_id, owner_id, dirtype = struct.unpack_from("<iiii", raw, pos)
            name, off = MidasStringCodec.decode_utf16_len1(raw, pos + 16)
            description, end = MidasStringCodec.decode_utf16_len2(raw, off)
            devices.append(
                DeviceDto(
                    device_id=name,
                    owner_id=owner_id,
                    directory_id=directory_id,
                    dirtype=dirtype,
                    description=description,
                )
            )
            # Observed MIDAS rows overlap by one zero byte before the next row.
            pos = end - 1
        return devices

    @staticmethod
    def parse_owners_dataset(raw: bytes) -> list[OwnerDto]:
        owners: list[OwnerDto] = []
        seen: set[int] = set()
        for marker in range(0, len(raw) - 16):
            if raw[marker] != 0x10:
                continue
            try:
                owner_id = struct.unpack_from("<i", raw, marker + 1)[0]
                name, off = MidasStringCodec.decode_utf16_len2(raw, marker + 5)
                short_name, _end = MidasStringCodec.decode_utf16_len2(raw, off)
            except Exception:
                continue
            if owner_id in seen:
                continue
            if 0 <= owner_id <= 100 and name.startswith("НГДУ") and short_name.startswith("НГДУ"):
                owners.append(OwnerDto(owner_id=owner_id, owner_name=name, owner_short_name=short_name))
                seen.add(owner_id)
        return sorted(owners, key=lambda item: item.owner_id)


class MeasureListDataParser:
    @staticmethod
    def extract_rows(listdata: bytes, *, owner_id: Optional[int] = None, device_id: Optional[int | str] = None) -> list[MeasureRowDto]:
        rows: list[MeasureRowDto] = []
        seen: set[tuple[int, int, int, int]] = set()
        device_id_int = int(device_id) if device_id is not None else None
        for off in range(0, max(0, len(listdata) - 16)):
            measure_id, oid, did, devtype = struct.unpack_from("<IIII", listdata, off)
            if measure_id <= 0 or measure_id > 50_000_000:
                continue
            if owner_id is not None and oid != owner_id:
                continue
            if device_id_int is not None and did != device_id_int:
                continue
            if not (0 <= devtype <= 100):
                continue
            key = (measure_id, oid, did, devtype)
            if key in seen:
                continue
            seen.add(key)
            rows.append(MeasureRowDto(measure_id=measure_id, owner_id=oid, device_id=did, device_type=devtype, offset=off))
        return sorted(rows, key=lambda row: row.measure_id, reverse=True)


class MeasurementBinaryParser:
    @staticmethod
    def detect_sample_offset(data: bytes) -> int:
        best_off = -1
        best_count = 0
        min_ts = int(dt.datetime(2020, 1, 1).timestamp())
        max_ts = int(dt.datetime(2040, 1, 1).timestamp())
        for off in range(0, min(len(data), 4096)):
            count = 0
            pos = off
            prev_ts = None
            while pos + 10 <= len(data):
                ts = struct.unpack_from(">I", data, pos)[0]
                channel = struct.unpack_from(">H", data, pos + 4)[0]
                if not (min_ts <= ts <= max_ts):
                    break
                if prev_ts is not None and (ts < prev_ts or ts - prev_ts > 3600):
                    break
                if channel == 0 or channel > 0xFFFF:
                    break
                count += 1
                prev_ts = ts
                pos += 10
            if count > best_count:
                best_count = count
                best_off = off
        if best_count < 10:
            raise ToucanDecodeError("could not detect telemetry sample section")
        return best_off

    @classmethod
    def parse(cls, data: bytes) -> MeasurementParsedDto:
        if len(data) < 16 or data[0:2] != b"\x55\xaa":
            raise ToucanDecodeError("not a TNOMeasureLoadView 0x55AA payload")
        sample_off = cls.detect_sample_offset(data)
        raw_records: list[MeasurementRecordDto] = []
        pos = sample_off
        while pos + 10 <= len(data):
            ts = struct.unpack_from(">I", data, pos)[0]
            channel = struct.unpack_from(">H", data, pos + 4)[0]
            raw = struct.unpack_from(">i", data, pos + 6)[0]
            name, scale, clamp_zero = CHANNEL_MAP.get(channel, (f"channel_{channel:04x}", 1.0, False))
            value = raw / scale
            if clamp_zero and value < 0:
                value = 0.0
            raw_records.append(
                MeasurementRecordDto(
                    timestamp=ts,
                    datetime=dt.datetime.fromtimestamp(ts),
                    channel=channel,
                    raw=raw,
                    value=value,
                    name=name,
                )
            )
            pos += 10

        by_ts: dict[int, dict[str, Any]] = {}
        for rec in raw_records:
            row = by_ts.setdefault(rec.timestamp, {"timestamp": rec.timestamp, "datetime": rec.datetime, "extra": {}})
            if rec.name in ("hook_weight_t", "h2s_mg_m3", "ch4_percent"):
                row[rec.name] = rec.value
                row[f"{rec.name}_raw"] = rec.raw
            else:
                row["extra"][rec.name or f"channel_{rec.channel:04x}"] = rec.value
                row["extra"][f"{rec.name or f'channel_{rec.channel:04x}'}_raw"] = rec.raw

        rows: list[MeasurementRowDto] = []
        for ts in sorted(by_ts):
            row = by_ts[ts]
            rows.append(
                MeasurementRowDto(
                    timestamp=row["timestamp"],
                    datetime=row["datetime"],
                    hook_weight_t=row.get("hook_weight_t"),
                    h2s_mg_m3=row.get("h2s_mg_m3"),
                    ch4_percent=row.get("ch4_percent"),
                    hook_weight_t_raw=row.get("hook_weight_t_raw"),
                    h2s_mg_m3_raw=row.get("h2s_mg_m3_raw"),
                    ch4_percent_raw=row.get("ch4_percent_raw"),
                    extra=row.get("extra", {}),
                )
            )

        ascii_text = "".join(chr(b) if 32 <= b < 127 else " " for b in data[:256])
        return MeasurementParsedDto(
            magic="55aa",
            sample_offset=sample_off,
            records_count=len(raw_records),
            channels=sorted({r.channel for r in raw_records}),
            start=raw_records[0].datetime if raw_records else None,
            end=raw_records[-1].datetime if raw_records else None,
            raw_records=raw_records,
            rows=rows,
            header_ascii_hint=" ".join(ascii_text.split()),
        )


class CsvMeasurementExporter:
    DEFAULT_FIELDS = [
        "timestamp",
        "datetime",
        "hook_weight_t",
        "h2s_mg_m3",
        "ch4_percent",
        "hook_weight_t_raw",
        "h2s_mg_m3_raw",
        "ch4_percent_raw",
    ]

    @classmethod
    def write_measurement(cls, measurement: MeasurementParsedDto, path: str | Path) -> None:
        path = Path(path)
        extra_fields = sorted({key for row in measurement.rows for key in row.extra.keys()})
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cls.DEFAULT_FIELDS + extra_fields)
            writer.writeheader()
            for row in measurement.rows:
                payload = asdict(row)
                extra = payload.pop("extra", {})
                payload.update(extra)
                if isinstance(payload.get("datetime"), dt.datetime):
                    payload["datetime"] = payload["datetime"].isoformat(sep=" ")
                writer.writerow(payload)

    @staticmethod
    def write_devices(devices: list[DeviceDto], path: str | Path) -> None:
        fields = ["owner_id", "owner_name", "owner_short_name", "device_id", "directory_id", "dirtype", "description"]
        with Path(path).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for d in devices:
                writer.writerow({key: getattr(d, key) for key in fields})

    @staticmethod
    def write_owners(owners: list[OwnerDto], path: str | Path) -> None:
        fields = ["owner_id", "owner_name", "owner_short_name"]
        with Path(path).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for o in owners:
                writer.writerow({key: getattr(o, key) for key in fields})
