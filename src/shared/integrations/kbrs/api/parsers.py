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
    ExtractedStringDto,
    MeasureRowDto,
    MeasurementDetailsDto,
    MeasurementEventDto,
    MeasurementFullDto,
    MeasurementParsedDto,
    MeasurementPassportDto,
    MeasurementRecordDto,
    MeasurementRowDto,
    NumericCandidateDto,
    OwnerDto,
    RawDatasetDto,
    WorkTypeDto,
)
from .exceptions import ToucanDecodeError, ToucanProtocolError


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


class BinaryExtractor:
    ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
    EVENT_KEYWORDS = (
        "remont",
        "podem",
        "pasport",
        "passport",
        "regim",
        "режим",
        "ремонт",
        "паспорт",
        "прибор",
        "вкл",
        "выкл",
    )
    WORK_TYPE_NAMES = {
        38: "Remont podemnika",
        42: "Regim v na obed",
    }
    EVENT_CHANNELS = {1, 2, 5, 83, 0x2001}

    @staticmethod
    def _looks_readable(text: str, *, min_len: int = 2) -> bool:
        text = text.strip("\x00 \t\r\n")
        if len(text) < min_len:
            return False
        bad = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\r\n")
        if bad:
            return False
        readable = sum(1 for ch in text if ch.isprintable())
        return readable / max(len(text), 1) > 0.85

    @classmethod
    def extract_ascii_strings(cls, data: bytes, *, min_len: int = 4) -> list[ExtractedStringDto]:
        out: list[ExtractedStringDto] = []
        for match in cls.ASCII_RE.finditer(data):
            text = match.group(0).decode("latin1", errors="replace").strip()
            if len(text) >= min_len:
                out.append(ExtractedStringDto(offset=match.start(), encoding="ascii", text=text))
        return out

    @classmethod
    def extract_prefixed_utf16_strings(cls, data: bytes, *, min_len: int = 2) -> list[ExtractedStringDto]:
        out: list[ExtractedStringDto] = []
        seen: set[tuple[int, str]] = set()
        for pos in range(0, len(data) - 2):
            for label, decoder in (
                ("utf16le_len1", MidasStringCodec.decode_utf16_len1),
                ("utf16le_len2", MidasStringCodec.decode_utf16_len2),
            ):
                try:
                    text, end = decoder(data, pos)
                except Exception:
                    continue
                text = text.strip("\x00 \t\r\n")
                if end <= pos or end - pos > 512:
                    continue
                if not cls._looks_readable(text, min_len=min_len):
                    continue
                key = (pos, text)
                if key in seen:
                    continue
                seen.add(key)
                out.append(ExtractedStringDto(offset=pos, encoding=label, text=text))
        return out

    @classmethod
    def extract_utf16_runs(cls, data: bytes, *, min_len: int = 4) -> list[ExtractedStringDto]:
        out: list[ExtractedStringDto] = []
        for start in (0, 1):
            pos = start
            while pos + 2 <= len(data):
                run_start = pos
                chars: list[str] = []
                while pos + 2 <= len(data):
                    code = struct.unpack_from("<H", data, pos)[0]
                    ch = chr(code)
                    if code in (9, 10, 13) or (32 <= code <= 0x04FF) or (0x2010 <= code <= 0x2122):
                        chars.append(ch)
                        pos += 2
                    else:
                        break
                text = "".join(chars).strip("\x00 \t\r\n")
                if cls._looks_readable(text, min_len=min_len):
                    out.append(ExtractedStringDto(offset=run_start, encoding="utf16le_run", text=text))
                pos = max(pos + 2, run_start + 2)
        return out

    @classmethod
    def extract_strings(cls, data: bytes) -> list[ExtractedStringDto]:
        items = (
            cls.extract_ascii_strings(data)
            + cls.extract_prefixed_utf16_strings(data)
            + cls.extract_utf16_runs(data)
        )
        best: dict[tuple[int, str], ExtractedStringDto] = {}
        for item in items:
            text = " ".join(item.text.split())
            if not text:
                continue
            key = (item.offset, text)
            current = best.get(key)
            if current is None or len(item.encoding) < len(current.encoding):
                best[key] = ExtractedStringDto(offset=item.offset, encoding=item.encoding, text=text)
        return sorted(best.values(), key=lambda item: (item.offset, item.encoding))

    @staticmethod
    def extract_numbers(data: bytes, *, limit: int = 5000) -> list[NumericCandidateDto]:
        out: list[NumericCandidateDto] = []
        for off in range(0, len(data) - 8, 4):
            i32 = struct.unpack_from("<i", data, off)[0]
            if -1_000_000 <= i32 <= 50_000_000 and i32 not in (0, -1):
                out.append(NumericCandidateDto(offset=off, type_name="int32_le", value=i32))
            u32 = struct.unpack_from("<I", data, off)[0]
            if 946684800 <= u32 <= 2208988800:
                out.append(NumericCandidateDto(offset=off, type_name="unix_ts_le", value=u32))
            f64 = struct.unpack_from("<d", data, off)[0]
            if -1_000_000.0 <= f64 <= 1_000_000.0 and abs(f64) > 0.000001:
                out.append(NumericCandidateDto(offset=off, type_name="double_le", value=f64))
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def utc_datetime_from_timestamp(ts: int) -> dt.datetime:
        return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).replace(tzinfo=None)

    @staticmethod
    def format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _event_text(channel: int, raw: int) -> tuple[Optional[int], str]:
        if channel == 1:
            return None, "ВКЛ Прибора"
        if channel == 2:
            return None, "ВЫКЛ Прибора"
        if channel == 5:
            name = BinaryExtractor.WORK_TYPE_NAMES.get(raw, f"WorkType {raw}")
            return raw, f"[{raw}] {name}"
        if channel == 83:
            return None, "Внешний накопитель"
        if channel == 0x2001:
            return None, "Паспорт: Вес на крюке"
        return None, f"channel_{channel:04x}: {raw}"

    @classmethod
    def extract_binary_events(cls, data: bytes, *, sample_offset: Optional[int]) -> list[MeasurementEventDto]:
        min_ts = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp())
        max_ts = int(dt.datetime(2040, 1, 1, tzinfo=dt.timezone.utc).timestamp())
        candidates: list[tuple[int, int, int, int]] = []

        header_end = sample_offset or min(len(data), 4096)
        for off in range(0, max(0, header_end - 9)):
            ts = struct.unpack_from(">I", data, off)[0]
            channel = struct.unpack_from(">H", data, off + 4)[0]
            raw = struct.unpack_from(">i", data, off + 6)[0]
            if min_ts <= ts <= max_ts and channel in cls.EVENT_CHANNELS:
                candidates.append((off, ts, channel, raw))

        if sample_offset is not None:
            pos = sample_offset
            while pos + 10 <= len(data):
                ts = struct.unpack_from(">I", data, pos)[0]
                channel = struct.unpack_from(">H", data, pos + 4)[0]
                raw = struct.unpack_from(">i", data, pos + 6)[0]
                if min_ts <= ts <= max_ts and channel in cls.EVENT_CHANNELS:
                    candidates.append((pos, ts, channel, raw))
                pos += 10

        candidates = sorted(set(candidates), key=lambda item: (item[1], item[0], item[2], item[3]))
        events: list[MeasurementEventDto] = []
        for index, (off, ts, channel, raw) in enumerate(candidates):
            code, text = cls._event_text(channel, raw)
            if channel == 5:
                next_ts = None
                for _off, other_ts, other_channel, _raw in candidates[index + 1:]:
                    if other_channel in {2, 5}:
                        next_ts = other_ts
                        break
                if next_ts is not None and next_ts >= ts:
                    text = f"{text} [{cls.format_duration(next_ts - ts)}]"
            event_dt = cls.utc_datetime_from_timestamp(ts)
            events.append(
                MeasurementEventDto(
                    offset=off,
                    time_text=event_dt.strftime("%H:%M:%S"),
                    code=code,
                    text=text,
                    raw_text=f"channel={channel} raw={raw}",
                )
            )
        return events

    @classmethod
    def extract_events(cls, strings: list[ExtractedStringDto]) -> list[MeasurementEventDto]:
        events: list[MeasurementEventDto] = []
        time_re = re.compile(r"\b([0-2]\d:[0-5]\d:[0-5]\d)\b")
        code_re = re.compile(r"\[(\d{1,5})\]")
        seen: set[tuple[int, str]] = set()
        for item in strings:
            lower = item.text.lower()
            has_event_shape = time_re.search(item.text) or any(keyword in lower for keyword in cls.EVENT_KEYWORDS)
            if not has_event_shape:
                continue
            code_match = code_re.search(item.text)
            time_match = time_re.search(item.text)
            clean_text = item.text
            if time_match:
                clean_text = clean_text.replace(time_match.group(1), "", 1).strip()
            key = (item.offset, item.text)
            if key in seen:
                continue
            seen.add(key)
            events.append(
                MeasurementEventDto(
                    offset=item.offset,
                    time_text=time_match.group(1) if time_match else None,
                    code=int(code_match.group(1)) if code_match else None,
                    text=clean_text,
                    raw_text=item.text,
                )
            )
        return events


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
        work_types = cls.parse_work_types_dataset(by_name["WorkTypesDataset"].raw) if "WorkTypesDataset" in by_name else []
        raw_datasets = cls.parse_raw_datasets(fields)
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
        return DirectoryDto(owners=owners, devices=enriched_devices, work_types=work_types, raw_datasets=raw_datasets)

    @staticmethod
    def parse_raw_datasets(fields: list[Any]) -> list[RawDatasetDto]:
        datasets: list[RawDatasetDto] = []
        for field in fields:
            strings = BinaryExtractor.extract_strings(field.raw)
            numbers = BinaryExtractor.extract_numbers(field.raw, limit=500)
            value = field.value if isinstance(field.value, str) else ""
            datasets.append(
                RawDatasetDto(
                    name=field.name,
                    type_code=field.type_code,
                    raw_size=len(field.raw),
                    value_preview=value[:200],
                    strings=strings,
                    numbers=numbers,
                )
            )
        return datasets

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

    @staticmethod
    def parse_work_types_dataset(raw: bytes) -> list[WorkTypeDto]:
        strings = BinaryExtractor.extract_strings(raw)
        meaningful = [
            item for item in strings
            if len(item.text) >= 2
            and item.text not in {"true", "false"}
            and not re.fullmatch(r"\d+", item.text)
        ]
        rows: list[WorkTypeDto] = []
        seen_names: set[str] = set()
        for item in meaningful:
            nearby_ints: list[int] = []
            for off in range(max(0, item.offset - 24), min(len(raw) - 4, item.offset + 4), 4):
                value = struct.unpack_from("<i", raw, off)[0]
                if 0 <= value <= 100_000 and value not in nearby_ints:
                    nearby_ints.append(value)
            work_type_id = nearby_ints[0] if nearby_ints else None
            name = item.text.strip()
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            rows.append(
                WorkTypeDto(
                    work_type_id=work_type_id,
                    name=name,
                    offset=item.offset,
                    raw_ints=nearby_ints,
                    raw_strings=[name],
                )
            )
        return rows


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
                    datetime=BinaryExtractor.utc_datetime_from_timestamp(ts),
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


class MeasurementDetailsParser:
    @staticmethod
    def _read_fixed_ascii_int(data: bytes, offset: int, size: int = 10, *, default: Optional[int] = None) -> Optional[int]:
        if offset + size > len(data):
            return default
        raw = data[offset:offset + size].replace(b"\x00", b"").strip()
        if not raw:
            return default
        try:
            return int(raw.decode("ascii"))
        except Exception:
            return default

    @staticmethod
    def _extract_passport(
        data: bytes,
        strings: list[ExtractedStringDto],
        numbers: list[NumericCandidateDto],
    ) -> MeasurementPassportDto:
        values: dict[str, Any] = {}
        for item in strings:
            text = item.text
            lower = text.lower()
            if "нгду" in lower and "organization" not in values:
                values["organization_candidate"] = text
            if re.search(r"\bv\d+\.\d+", text, re.IGNORECASE):
                values["device_version_candidate"] = text
            if "дэл" in lower or "del" in lower:
                values.setdefault("device_candidate", text)
            if "скваж" in lower:
                values.setdefault("well_text_candidate", text)
            if "бригада" in lower:
                values.setdefault("brigade_text_candidate", text)
            if "цех" in lower:
                values.setdefault("workshop_text_candidate", text)

        numeric_values = [n for n in numbers if n.type_name == "int32_le"]
        small_ints = [int(n.value) for n in numeric_values if 0 <= int(n.value) <= 10000]
        if small_ints:
            values["small_int_candidates"] = small_ints[:200]

        organization = values.get("organization_candidate")
        device_version = None
        version_source = values.get("device_version_candidate")
        if isinstance(version_source, str):
            match = re.search(r"\bv(\d+\.\d+)", version_source, re.IGNORECASE)
            if match:
                device_version = match.group(1)

        if len(data) >= 0x5C and data[:2] == b"\x55\xaa":
            values["header_size_or_flags"] = struct.unpack_from(">H", data, 2)[0]
            values["device_type"] = data[12] if len(data) > 12 else None
            values["measurement_timestamp"] = struct.unpack_from(">I", data, 8)[0]
            values["measurement_datetime"] = BinaryExtractor.utc_datetime_from_timestamp(
                int(values["measurement_timestamp"]),
            ).isoformat(sep=" ")
            if len(data) >= 17:
                values["device_id_be_offset_13"] = struct.unpack_from(">I", data, 13)[0]
            if len(data) >= 8:
                device_version = f"{data[6]:02x}.{data[7]:02x}"

            # DEL-150 measurement header uses null-padded ASCII slots.
            spu = MeasurementDetailsParser._read_fixed_ascii_int(data, 0x17, default=0)
            workshop = MeasurementDetailsParser._read_fixed_ascii_int(data, 0x21)
            brigade = MeasurementDetailsParser._read_fixed_ascii_int(data, 0x2B)
            field_id = MeasurementDetailsParser._read_fixed_ascii_int(data, 0x35)
            bush = MeasurementDetailsParser._read_fixed_ascii_int(data, 0x3F)
            well = MeasurementDetailsParser._read_fixed_ascii_int(data, 0x49)

            tare_weight_t = struct.unpack_from(">H", data, 0x55)[0] / 1000 if len(data) >= 0x57 else None
            tackle_block_ratio = data[0x57] if len(data) > 0x57 else None
            max_hook_weight_t = struct.unpack_from(">I", data, 0x58)[0] / 1000 if len(data) >= 0x5C else None

            return MeasurementPassportDto(
                device_id=values.get("device_id_be_offset_13") if isinstance(values.get("device_id_be_offset_13"), int) else None,
                device_version=device_version,
                organization=organization if isinstance(organization, str) else None,
                workshop=workshop,
                brigade=brigade,
                spu=spu,
                field_id=field_id,
                bush=bush,
                well=well,
                max_hook_weight_t=max_hook_weight_t,
                tackle_block_ratio=tackle_block_ratio,
                tare_weight_t=tare_weight_t,
                values=values,
            )

        return MeasurementPassportDto(
            organization=organization if isinstance(organization, str) else None,
            device_version=device_version,
            values=values,
        )

    @classmethod
    def parse(cls, data: bytes) -> MeasurementDetailsDto:
        magic = data[:2].hex() if len(data) >= 2 else ""
        try:
            sample_offset: Optional[int] = MeasurementBinaryParser.detect_sample_offset(data)
        except Exception:
            sample_offset = None
        header = data[:sample_offset] if sample_offset is not None else data
        strings = BinaryExtractor.extract_strings(header)
        numbers = BinaryExtractor.extract_numbers(header)
        events = BinaryExtractor.extract_binary_events(data, sample_offset=sample_offset)
        if not events:
            events = BinaryExtractor.extract_events(strings)
        ascii_text = "".join(chr(b) if 32 <= b < 127 else " " for b in header[:512])
        return MeasurementDetailsDto(
            magic=magic,
            raw_size=len(data),
            sample_offset=sample_offset,
            passport=cls._extract_passport(header, strings, numbers),
            events=events,
            strings=strings,
            numbers=numbers,
            header_ascii_hint=" ".join(ascii_text.split()),
        )


def unwrap_measure_payload(data: bytes) -> bytes:
    """Блок 0x55AA замера — и завершённого, и ещё пишущегося.

    Завершённый замер ``TNOMeasureLoadView`` отдаёт голым блоком 0x55AA.
    Пока замер пишется, приходит RPC-ответ: ``Measure`` → ``DataBinary``
    (int32 длины + тот же блок). Непохожее на обёртку возвращается как есть —
    парсер ниже сам скажет, что это не замер.
    """
    if data[:2] == b"\x55\xaa":
        return data
    try:
        measure = TlvCodec.parse_rpc_response(data).result.get("Measure")
        off = 0
        # Поля идут до DataBinary; следующее за ним DataCompressed пишется с
        # флагом в старшем бите длины, которого TlvCodec не понимает.
        while measure is not None and off < len(measure.raw):
            field, off = TlvCodec.parse_one_field(measure.raw, off)
            if field is None:
                break
            if field.name == "DataBinary":
                size = struct.unpack_from("<i", field.raw, 0)[0]
                return field.raw[4:4 + size]
    except (ToucanProtocolError, struct.error):
        pass
    return data


class MeasurementFullParser:
    @staticmethod
    def parse(data: bytes) -> MeasurementFullDto:
        data = unwrap_measure_payload(data)
        return MeasurementFullDto(
            chart=MeasurementBinaryParser.parse(data),
            details=MeasurementDetailsParser.parse(data),
        )


class MeasurementPassportPeeker:
    """Cheap passport field extraction — no chart/events/strings scan.

    Used by hot matching paths (e.g. SPO fetcher) where we only need to
    check whether a raw measurement belongs to the target well before
    deciding whether to run the full parser.
    """

    # DEL-150 header layout, mirrors MeasurementDetailsParser._extract_passport.
    _WELL_OFFSET = 0x49
    _WELL_SIZE = 10
    _MAGIC = b"\x55\xaa"

    @classmethod
    def read_well(cls, data: bytes) -> int | None:
        data = unwrap_measure_payload(data)
        end = cls._WELL_OFFSET + cls._WELL_SIZE
        if len(data) < end or data[:2] != cls._MAGIC:
            return None
        raw = data[cls._WELL_OFFSET:end].replace(b"\x00", b"").strip()
        if not raw:
            return None
        try:
            return int(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None


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
