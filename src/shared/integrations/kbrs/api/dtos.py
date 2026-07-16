from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

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
    work_type_id: int | None
    name: str
    short_name: str | None = None
    description: str | None = None
    offset: int | None = None
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
    owner_id: int | None = None
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class MeasureListFilterDto:
    owner_id: int | None = None
    device_id: str | None = None
    list_condition: str | None = None
    tree_group_type: TreeGroupType = TreeGroupType.DEVICES
    condition_date: MeasureDateCondition = MeasureDateCondition.MONTH
    condition_type: MeasureConditionType = MeasureConditionType.ACTUAL
    date_from: dt.datetime | None = None
    date_to: dt.datetime | None = None
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
    ) -> MeasureListFilterDto:
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
    value: float | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class MeasurementRowDto:
    timestamp: int
    datetime: dt.datetime
    hook_weight_t: float | None = None
    h2s_mg_m3: float | None = None
    ch4_percent: float | None = None
    hook_weight_t_raw: int | None = None
    h2s_mg_m3_raw: int | None = None
    ch4_percent_raw: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementParsedDto:
    magic: str
    sample_offset: int
    records_count: int
    channels: list[int]
    start: dt.datetime | None
    end: dt.datetime | None
    raw_records: list[MeasurementRecordDto]
    rows: list[MeasurementRowDto]
    header_ascii_hint: str = ""


@dataclass(frozen=True, slots=True)
class MeasurementEventDto:
    offset: int
    time_text: str | None
    code: int | None
    text: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class MeasurementPassportDto:
    # № прибора / DEVICEID.
    # В системе отображается как: "Прибор: ДЭЛ150 №11292".
    device_id: int | None = None

    # Версия прибора.
    # В системе отображается рядом с прибором: "v11.29", "v12.32".
    device_version: str | None = None

    # Организация / НГДУ.
    # Например: "НГДУ Жайыкмунайгаз", "НГДУ Жылыоймунайгаз".
    # Обычно берётся не из measure.bin, а из OWNERID через OwnersDataset.
    organization: str | None = None

    # Цех.
    # В системе отображается как: "Цех: 2".
    workshop: int | None = None

    # Бригада.
    # В системе отображается как: "Бригада: 2".
    brigade: int | None = None

    # СПУ.
    # В системе отображается как: "СПУ: 0" или строковое значение типа "eEEe1".
    # Лучше хранить как str, потому что значение не всегда числовое.
    spu: str | None = None

    # Месторождение.
    # В системе отображается как: "Месторожд.: 5".
    field_id: int | None = None

    # Куст.
    # В системе отображается как: "Куст: 2".
    bush: int | None = None

    # Скважина.
    # В системе отображается как: "Скважина: 332".
    well: int | None = None

    # Максимальная нагрузка на крюк, тс.
    # В системе отображается как: "Макс.нагрузка на крюк: 40,000 тс".
    max_hook_weight_t: float | None = None

    # Передаточное число талевого блока.
    # В системе отображается как: "Перед.число талевого блока: 6".
    tackle_block_ratio: int | None = None

    # Вес талевой системы, тс.
    # В системе отображается как: "Вес талевой системы: 0,816 тс".
    tare_weight_t: float | None = None

    # Дополнительные/служебные значения, которые пока не замаплены в отдельные поля.
    # Пользователю обычно не показываем, но сохраняем для диагностики и будущего расширения парсера.
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementDetailsDto:
    magic: str
    raw_size: int
    sample_offset: int | None
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
    start: dt.datetime | None
    end: dt.datetime | None
    channels: list[int]
