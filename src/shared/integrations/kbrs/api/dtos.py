from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Optional

from .constants import DEFAULT_VERSION
from .enums import (
    MeasureConditionType,
    MeasureDateCondition,
    MeasureDirection,
    MeasureOperation,
    TreeGroupType,
)


@dataclass(frozen=True, slots=True)
class ToucanCredentialsDto:
    login: str
    password: str
    version: str = DEFAULT_VERSION
    machine_unique: str = "PY-TOUCAN"
    computer_name: str = "PYTHON-CLIENT"
    host_name: str = "PYTHON-CLIENT"
    client_user_agent: str = ""


@dataclass(frozen=True, slots=True)
class RpcFieldDto:
    name: str
    type_code: int
    raw: bytes
    value: Any
    offset: int
    end: int


@dataclass(frozen=True, slots=True)
class RpcResponseDto:
    header: dict[str, Any]
    method: str
    result: dict[str, RpcFieldDto]


@dataclass(frozen=True, slots=True)
class OwnerDto:
    owner_id: int
    owner_name: str
    owner_short_name: str


@dataclass(frozen=True, slots=True)
class DeviceDto:
    device_id: str
    owner_id: int
    directory_id: int
    dirtype: int
    description: str
    owner_name: str = ""
    owner_short_name: str = ""


@dataclass(frozen=True, slots=True)
class ExtractedStringDto:
    offset: int
    encoding: str
    text: str


@dataclass(frozen=True, slots=True)
class NumericCandidateDto:
    offset: int
    type_name: str
    value: int | float


@dataclass(frozen=True, slots=True)
class RawDatasetDto:
    name: str
    type_code: int
    raw_size: int
    value_preview: str = ""
    strings: list[ExtractedStringDto] = field(default_factory=list)
    numbers: list[NumericCandidateDto] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WorkTypeDto:
    work_type_id: Optional[int]
    name: str
    short_name: Optional[str] = None
    description: Optional[str] = None
    offset: Optional[int] = None
    raw_ints: list[int] = field(default_factory=list)
    raw_strings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DirectoryDto:
    owners: list[OwnerDto] = field(default_factory=list)
    devices: list[DeviceDto] = field(default_factory=list)
    work_types: list[WorkTypeDto] = field(default_factory=list)
    raw_datasets: list[RawDatasetDto] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DeviceSearchFilterDto:
    query: str = ""
    owner_id: Optional[int] = None
    device_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class MeasureListFilterDto:
    owner_id: Optional[int] = None
    device_id: Optional[str] = None
    list_condition: Optional[str] = None
    tree_group_type: TreeGroupType = TreeGroupType.DEVICES
    condition_date: MeasureDateCondition = MeasureDateCondition.MONTH
    condition_type: MeasureConditionType = MeasureConditionType.ACTUAL
    date_from: Optional[dt.datetime] = None
    date_to: Optional[dt.datetime] = None
    search_text: str = ""
    sort_by_measure_id: bool = False
    list_only: bool = True
    page_count: int = 50
    operation: MeasureOperation = MeasureOperation.NONE
    operation_measure_id: int = 0
    operation_measure_ids: str = ""
    contains_operation_measure_params: bool = False

    @classmethod
    def for_day(
        cls,
        *,
        owner_id: int,
        device_id: int | str,
        day: dt.date,
        page_count: int = 50,
    ) -> "MeasureListFilterDto":
        return cls(
            owner_id=owner_id,
            device_id=str(device_id),
            condition_date=MeasureDateCondition.INTERVAL,
            date_from=dt.datetime.combine(day, dt.time.min),
            date_to=dt.datetime.combine(day, dt.time(23, 59, 59)),
            page_count=page_count,
        )

    @property
    def effective_list_condition(self) -> str:
        if self.list_condition is not None:
            return self.list_condition
        parts: list[str] = []
        if self.owner_id is not None:
            parts.append(f"OWNERID={int(self.owner_id)}")
        if self.device_id is not None:
            parts.append(f"DEVICEID={int(self.device_id)}")
        return " AND ".join(parts)


@dataclass(frozen=True, slots=True)
class MeasureRowDto:
    measure_id: int
    owner_id: int
    device_id: int
    device_type: int
    offset: int


@dataclass(frozen=True, slots=True)
class LoadMeasurementRequestDto:
    measure_id: int
    device_id: int = 0
    on_datetime: float = 0.0
    direction: MeasureDirection = MeasureDirection.NONE
    update_offset: int = 0


@dataclass(frozen=True, slots=True)
class MeasurementRecordDto:
    timestamp: int
    datetime: dt.datetime
    channel: int
    raw: int
    value: Optional[float] = None
    name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class MeasurementRowDto:
    timestamp: int
    datetime: dt.datetime
    hook_weight_t: Optional[float] = None
    h2s_mg_m3: Optional[float] = None
    ch4_percent: Optional[float] = None
    hook_weight_t_raw: Optional[int] = None
    h2s_mg_m3_raw: Optional[int] = None
    ch4_percent_raw: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementParsedDto:
    magic: str
    sample_offset: int
    records_count: int
    channels: list[int]
    start: Optional[dt.datetime]
    end: Optional[dt.datetime]
    raw_records: list[MeasurementRecordDto]
    rows: list[MeasurementRowDto]
    header_ascii_hint: str = ""


@dataclass(frozen=True, slots=True)
class MeasurementEventDto:
    offset: int
    time_text: Optional[str]
    code: Optional[int]
    text: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class MeasurementPassportDto:
    device_id: Optional[int] = None
    device_version: Optional[str] = None
    organization: Optional[str] = None
    workshop: Optional[int] = None
    brigade: Optional[int] = None
    spu: Optional[int] = None
    field_id: Optional[int] = None
    bush: Optional[int] = None
    well: Optional[int] = None
    max_hook_weight_t: Optional[float] = None
    tackle_block_ratio: Optional[int] = None
    tare_weight_t: Optional[float] = None
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementDetailsDto:
    magic: str
    raw_size: int
    sample_offset: Optional[int]
    passport: MeasurementPassportDto
    events: list[MeasurementEventDto] = field(default_factory=list)
    strings: list[ExtractedStringDto] = field(default_factory=list)
    numbers: list[NumericCandidateDto] = field(default_factory=list)
    header_ascii_hint: str = ""


@dataclass(frozen=True, slots=True)
class MeasurementFullDto:
    chart: MeasurementParsedDto
    details: MeasurementDetailsDto


@dataclass(frozen=True, slots=True)
class ExportMeasurementResultDto:
    measure_id: int
    csv_path: str
    records_count: int
    row_count: int
    start: Optional[dt.datetime]
    end: Optional[dt.datetime]
    channels: list[int]
