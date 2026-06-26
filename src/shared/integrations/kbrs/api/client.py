from __future__ import annotations

import datetime as dt
from pathlib import Path

from .config import ToucanClientConfig
from .dtos import (
    DeviceDto,
    DeviceSearchFilterDto,
    DirectoryDto,
    ExportMeasurementResultDto,
    LoadMeasurementRequestDto,
    MeasureListFilterDto,
    MeasurementParsedDto,
    MeasureRowDto,
    OwnerDto,
    ToucanCredentialsDto,
)
from .exceptions import ToucanNotFoundError
from .parsers import CsvMeasurementExporter
from .services import (
    ToucanAuthService,
    ToucanDirectoryService,
    ToucanMeasurementService,
)
from .transport import ToucanRpcTransport


class ToucanBackendClient:
    """Backend-oriented facade for Toucan RPC operations.

    This class hides the binary RPC/TLV transport and exposes typed DTOs that are
    convenient to call from service layers, FastAPI routes, jobs, or workers.
    """

    def __init__(self, config: ToucanClientConfig):
        self.config = config
        self.session_id = ""
        self._transport = ToucanRpcTransport(config)
        self._auth_service = ToucanAuthService(self._transport)
        self.directory_service = ToucanDirectoryService()
        self.measurement_service = ToucanMeasurementService(self._transport)

    def __enter__(self) -> ToucanBackendClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def close(self) -> None:
        self._transport.close()

    @property
    def directory(self) -> DirectoryDto:
        return self.directory_service.directory

    def login(self, credentials: ToucanCredentialsDto) -> str:
        self.session_id, directory = self._auth_service.login(credentials)
        self.directory_service.update(directory)
        return self.session_id

    def list_owners(self) -> list[OwnerDto]:
        return self.directory_service.list_owners()

    def list_devices(self, owner_id: int | None = None) -> list[DeviceDto]:
        return self.directory_service.list_devices(owner_id=owner_id)

    def search_devices(
        self,
        query: str = "",
        *,
        owner_id: int | None = None,
        device_id: int | str | None = None,
    ) -> list[DeviceDto]:
        return self.directory_service.search_devices(
            DeviceSearchFilterDto(
                query=query,
                owner_id=owner_id,
                device_id=str(device_id) if device_id is not None else None,
            ),
        )

    def resolve_owner_for_device(self, device_id: int | str) -> int:
        return self.directory_service.resolve_owner_for_device(device_id)

    def list_measures(self, filters: MeasureListFilterDto) -> list[MeasureRowDto]:
        return self.measurement_service.list_measures(filters)

    def list_measures_for_day(
        self,
        *,
        device_id: int | str,
        day: dt.date,
        owner_id: int | None = None,
        page_count: int = 50,
    ) -> list[MeasureRowDto]:
        resolved_owner_id = (
            owner_id
            if owner_id is not None
            else self.resolve_owner_for_device(device_id)
        )
        return self.list_measures(
            MeasureListFilterDto.for_day(
                owner_id=resolved_owner_id,
                device_id=device_id,
                day=day,
                page_count=page_count,
            ),
        )

    def load_measurement(
        self,
        request: LoadMeasurementRequestDto,
    ) -> MeasurementParsedDto:
        return self.measurement_service.load_measurement(request)

    def load_latest_measuremload_latest_measurement_for_dayent_for_day(
        self,
        *,
        device_id: int | str,
        day: dt.date,
        owner_id: int | None = None,
        page_count: int = 50,
    ) -> tuple[MeasureRowDto, MeasurementParsedDto]:
        measures = self.list_measures_for_day(
            device_id=device_id,
            day=day,
            owner_id=owner_id,
            page_count=page_count,
        )
        if not measures:
            raise ToucanNotFoundError(
                f"No measurements found for DEVICEID={device_id} on {day.isoformat()}",
            )
        selected = measures[0]
        parsed = self.load_measurement(
            LoadMeasurementRequestDto(measure_id=selected.measure_id),
        )
        return selected, parsed

    def export_latest_measurement_for_day_csv(
        self,
        *,
        device_id: int | str,
        day: dt.date,
        csv_path: str | Path,
        owner_id: int | None = None,
        page_count: int = 50,
    ) -> ExportMeasurementResultDto:
        measures = self.list_measures_for_day(
            device_id=device_id,
            day=day,
            owner_id=owner_id,
            page_count=page_count,
        )
        if not measures:
            raise ToucanNotFoundError(
                f"No measurements found for DEVICEID={device_id} on {day.isoformat()}",
            )
        selected = measures[0]
        return self.measurement_service.export_measurement_csv(
            request=LoadMeasurementRequestDto(measure_id=selected.measure_id),
            csv_path=csv_path,
        )

    def export_devices_csv(self, path: str | Path) -> None:
        CsvMeasurementExporter.write_devices(self.list_devices(), path)

    def export_owners_csv(self, path: str | Path) -> None:
        CsvMeasurementExporter.write_owners(self.list_owners(), path)
