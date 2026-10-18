from datetime import datetime
from typing import Any

from shared.dto.repositories import RepositoryDTO


class CreateGpsUnitDTO(RepositoryDTO):
    """Поля машины из справочника Аргуса; ``tracked`` и курсор трека ведёт
    загрузчик треков, синхронизация справочника их не трогает."""

    argus_id: int
    name: str
    plate_normalized: str | None = None
    imei: str | None = None
    hw_type: str | None = None
    equipment: str | None = None
    group_name: str | None = None
    vehicle_type: str | None = None
    vehicle_model: str | None = None
    is_lift: bool = False
    cf: dict[str, Any] | None = None
    sensors: list[Any] | None = None
    last_message_at: datetime | None = None


class UpdateGpsUnitDTO(RepositoryDTO):
    name: str | None = None
    plate_normalized: str | None = None
    imei: str | None = None
    hw_type: str | None = None
    equipment: str | None = None
    group_name: str | None = None
    vehicle_type: str | None = None
    vehicle_model: str | None = None
    is_lift: bool | None = None
    cf: dict[str, Any] | None = None
    sensors: list[Any] | None = None
    last_message_at: datetime | None = None
    tracked: bool | None = None
    track_cursor_at: datetime | None = None
    removed_at: datetime | None = None


class CreateGpsMessageDTO(RepositoryDTO):
    unit_id: int
    time: datetime
    lat: float
    lon: float
    speed: int | None = None
    course: int | None = None
    height: int | None = None
    sats: int | None = None
    ignition: bool | None = None
    received_at: datetime | None = None


class UpdateGpsMessageDTO(RepositoryDTO):
    """Точки трека не обновляются — только вставка с пропуском повторов."""
