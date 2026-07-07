from __future__ import annotations

import datetime as dt
from pathlib import Path

from .codec import DelphiDateTimeCodec, TlvCodec
from .dtos import (
    DeviceDto,
    DeviceSearchFilterDto,
    DirectoryDto,
    ExportMeasurementResultDto,
    LoadMeasurementRequestDto,
    MeasureListFilterDto,
    MeasurementDetailsDto,
    MeasurementFullDto,
    MeasurementParsedDto,
    MeasureRowDto,
    OwnerDto,
    ToucanCredentialsDto,
    WorkTypeDto,
)
from .exceptions import ToucanAuthenticationError, ToucanNotFoundError
from .parsers import (
    CsvMeasurementExporter,
    DirectoryDataParser,
    MeasureListDataParser,
    MeasurementBinaryParser,
    MeasurementDetailsParser,
    MeasurementFullParser,
)
from .transport import ToucanRpcTransport


class ToucanAuthService:
    def __init__(self, transport: ToucanRpcTransport):
        self._transport = transport

    def login(self, credentials: ToucanCredentialsDto) -> tuple[str, DirectoryDto]:
        response = self._transport.call_rpc(
            "TNOConnect",
            [
                TlvCodec.wstr("Version", credentials.version),
                TlvCodec.wstr("UserLogin", credentials.login),
                TlvCodec.wstr("Password", credentials.password),
                TlvCodec.wstr("MachineUnique", credentials.machine_unique),
                TlvCodec.wstr("ComputerName", credentials.computer_name),
                TlvCodec.wstr("HostName", credentials.host_name),
                TlvCodec.wstr("ClientUserAgent", credentials.client_user_agent),
            ],
            sid="",
        )
        sid_field = response.result.get("CreatedSessionID")
        if sid_field is None:
            raise ToucanAuthenticationError(
                "TNOConnect did not return CreatedSessionID",
            )
        session_id = str(sid_field.value)
        self._transport.set_session_id(session_id)

        directory_field = response.result.get("DirectoryData")
        directory = (
            DirectoryDataParser.parse(directory_field.raw)
            if directory_field is not None
            else DirectoryDto()
        )
        return session_id, directory


class ToucanDirectoryService:
    def __init__(self, directory: DirectoryDto | None = None):
        self._directory = directory or DirectoryDto()

    @property
    def directory(self) -> DirectoryDto:
        return self._directory

    def update(self, directory: DirectoryDto) -> None:
        self._directory = directory

    def list_owners(self) -> list[OwnerDto]:
        return list(self._directory.owners)

    def list_devices(self, owner_id: int | None = None) -> list[DeviceDto]:
        if owner_id is None:
            return list(self._directory.devices)
        return [
            device for device in self._directory.devices if device.owner_id == owner_id
        ]

    def list_work_types(self) -> list[WorkTypeDto]:
        return list(self._directory.work_types)

    def search_devices(self, filters: DeviceSearchFilterDto) -> list[DeviceDto]:
        q = filters.query.lower().strip()
        out: list[DeviceDto] = []
        for d in self._directory.devices:
            if filters.owner_id is not None and d.owner_id != filters.owner_id:
                continue
            if filters.device_id is not None and str(d.device_id) != str(
                filters.device_id,
            ):
                continue
            hay = " ".join(
                [d.device_id, d.description, d.owner_name, d.owner_short_name],
            ).lower()
            if q and q not in hay:
                continue
            out.append(d)
        return out

    def resolve_owner_for_device(self, device_id: int | str) -> int:
        matches = self.search_devices(DeviceSearchFilterDto(device_id=str(device_id)))
        if len(matches) > 1:
            matches = [matches[0]]
            print("Warning: multiple matches found for device_id", device_id)
        if len(matches) == 1:
            return matches[0].owner_id
        if not matches:
            raise ToucanNotFoundError(
                f"DEVICEID={device_id} not found in DirectoryData",
            )
        owner_ids = ", ".join(str(m.owner_id) for m in matches)
        raise ToucanNotFoundError(
            f"DEVICEID={device_id} exists under multiple owners: {owner_ids}; pass owner_id explicitly",
        )


class ToucanMeasurementService:
    def __init__(self, transport: ToucanRpcTransport):
        self._transport = transport

    def list_measures(self, filters: MeasureListFilterDto) -> list[MeasureRowDto]:
        date_condition = filters.condition_date
        date_from = filters.date_from
        date_to = filters.date_to
        if date_from is not None or date_to is not None:
            date_condition = type(filters.condition_date).INTERVAL
            if date_from is None:
                date_from = dt.datetime.now() - dt.timedelta(days=30)
            if date_to is None:
                date_to = dt.datetime.now()

        condition_start = DelphiDateTimeCodec.to_delphi(
            date_from or (dt.datetime.now() - dt.timedelta(days=30)),
        )
        condition_end = DelphiDateTimeCodec.to_delphi(
            date_to or (dt.datetime.now() + dt.timedelta(days=1)),
        )

        response = self._transport.call_rpc(
            "TNOMeasureList",
            [
                TlvCodec.enum("TreeGroupType", filters.tree_group_type.value),
                TlvCodec.enum("ConditionDate", date_condition.value),
                TlvCodec.enum("ConditionType", filters.condition_type.value),
                TlvCodec.double("ConditionDateIntervalStart", condition_start),
                TlvCodec.double("ConditionDateIntervalEnd", condition_end),
                TlvCodec.wstr("SearchText", filters.search_text),
                TlvCodec.enum("SortByMeasureID", filters.sort_by_measure_id),
                TlvCodec.enum("ListOnly", filters.list_only),
                TlvCodec.wstr("ListCondition", filters.effective_list_condition),
                TlvCodec.int32("ListPageCount", filters.page_count),
                TlvCodec.enum("Operation", filters.operation.value),
                TlvCodec.int32("OperationMeasureID", filters.operation_measure_id),
                TlvCodec.wstr("OperationMeasureIDs", filters.operation_measure_ids),
                TlvCodec.enum(
                    "ContainsOperationMeasureParams",
                    filters.contains_operation_measure_params,
                ),
            ],
        )
        listdata = response.result.get("ListData")
        if listdata is None:
            return []
        return MeasureListDataParser.extract_rows(
            listdata.raw,
            owner_id=filters.owner_id,
            device_id=filters.device_id,
        )

    def load_raw_measurement(self, request: LoadMeasurementRequestDto) -> bytes:
        return self._transport.call_raw(
            "TNOMeasureLoadView",
            [
                TlvCodec.int32("MeasureID", request.measure_id),
                TlvCodec.int32("DeviceID", request.device_id),
                TlvCodec.double("OnDateTime", request.on_datetime),
                TlvCodec.enum("Direction", request.direction.value),
                TlvCodec.int32("UpdateOffset", request.update_offset),
            ],
        )

    def load_measurement(
        self,
        request: LoadMeasurementRequestDto,
    ) -> MeasurementParsedDto:
        raw = self.load_raw_measurement(request)
        return MeasurementBinaryParser.parse(raw)

    def load_measurement_details(
        self,
        request: LoadMeasurementRequestDto,
    ) -> MeasurementDetailsDto:
        raw = self.load_raw_measurement(request)
        return MeasurementDetailsParser.parse(raw)

    def load_full_measurement(
        self,
        request: LoadMeasurementRequestDto,
    ) -> MeasurementFullDto:
        raw = self.load_raw_measurement(request)
        return MeasurementFullParser.parse(raw)

    def export_measurement_csv(
        self,
        *,
        request: LoadMeasurementRequestDto,
        csv_path: str | Path,
    ) -> ExportMeasurementResultDto:
        parsed = self.load_measurement(request)
        CsvMeasurementExporter.write_measurement(parsed, csv_path)
        return ExportMeasurementResultDto(
            measure_id=request.measure_id,
            csv_path=str(csv_path),
            records_count=parsed.records_count,
            row_count=len(parsed.rows),
            start=parsed.start,
            end=parsed.end,
            channels=parsed.channels,
        )
