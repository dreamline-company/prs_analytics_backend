"""Ответы «Аргуса» → строки ``gps_unit`` / ``gps_message``.

Время Аргуса — unix UTC; в БД — местное naive (Атырау), как остальные
времена приложения.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from apps.gps.dto.internal.repositories.gps import (
    CreateGpsMessageDTO,
    CreateGpsUnitDTO,
)
from apps.gps.services.plates import canonical_plate
from core.settings import get_settings
from shared.integrations.argus.api import ArgusMessageDto, ArgusUnitDto
from shared.integrations.argus.api.dtos import to_int

settings = get_settings()

# Точка «из будущего» дальше этого допуска — сбитые часы трекера.
FUTURE_TOLERANCE = timedelta(minutes=10)
# Ключи зажигания в params: Вега/УМКа — ign, Teltonika — io_239.
IGNITION_KEYS = ("ign", "io_239")

_SMALLINT_MIN, _SMALLINT_MAX = -32768, 32767
_MAX_LAT, _MAX_LON = 90, 180
# Подъёмник ПРС по типу/модели АТС: «ПОДЪЕМНИК АПРС - 40», «ПТП-40», «ПАП-80».
_LIFT_RE = re.compile(r"ПОДЪ[ЕЁ]МНИК|АПРС|ПТП|ПАП[\s-]*\d")


def from_unix(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=settings.ZONE_INFO).replace(tzinfo=None)


def to_unix(value: datetime) -> int:
    return int(value.replace(tzinfo=settings.ZONE_INFO).timestamp())


def parse_params(raw: str | None) -> dict[str, str]:
    """``"ign:0;ext_voltage:25.66;…"`` → ``{"ign": "0", "ext_voltage": "25.66"}``."""
    if not raw:
        return {}
    params: dict[str, str] = {}
    for pair in raw.split(";"):
        key, sep, value = pair.partition(":")
        if sep and key.strip():
            params[key.strip()] = value.strip()
    return params


def ignition(params: dict[str, str]) -> bool | None:
    for key in IGNITION_KEYS:
        value = params.get(key)
        if value is None:
            continue
        try:
            return float(value) != 0
        except ValueError:
            continue
    return None


def _smallint(value: int | None) -> int | None:
    if value is None or not _SMALLINT_MIN <= value <= _SMALLINT_MAX:
        return None
    return value


def has_fix(message: ArgusMessageDto) -> bool:
    """Без фикса трекер шлёт координаты 0/0 и ``sats = 0``."""
    if message.lat is None or message.lon is None:
        return False
    if message.sats is not None and message.sats <= 0:
        return False
    if abs(message.lat) > _MAX_LAT or abs(message.lon) > _MAX_LON:
        return False
    return not (message.lat == 0 and message.lon == 0)


def message_dto(
    unit_id: int,
    message: ArgusMessageDto,
    *,
    now: datetime,
) -> CreateGpsMessageDTO | None:
    """``None`` — точку не храним: нет фикса или время из будущего."""
    if not has_fix(message):
        return None
    time = from_unix(message.time)
    if time > now + FUTURE_TOLERANCE:
        return None
    params = parse_params(message.params)
    received = to_int(params.get("rcv_tm"))
    return CreateGpsMessageDTO(
        unit_id=unit_id,
        time=time,
        lat=message.lat,
        lon=message.lon,
        speed=_smallint(message.speed),
        course=_smallint(message.course),
        height=_smallint(message.height),
        sats=_smallint(message.sats),
        ignition=ignition(params),
        received_at=from_unix(received) if received else None,
    )


def message_dtos(
    unit_id: int,
    messages: Iterable[ArgusMessageDto],
    *,
    now: datetime,
) -> list[CreateGpsMessageDTO]:
    """Точки трека для вставки: отфильтрованы, одна на секунду (первая)."""
    by_time: dict[datetime, CreateGpsMessageDTO] = {}
    for message in messages:
        dto = message_dto(unit_id, message, now=now)
        if dto is not None:
            by_time.setdefault(dto.time, dto)
    return list(by_time.values())


def is_lift(*texts: str | None) -> bool:
    return any(text and _LIFT_RE.search(text.upper()) for text in texts)


def unit_dto(unit: ArgusUnitDto) -> CreateGpsUnitDTO:
    cf = unit.cf
    vehicle_type = cf.get("cf_Тип АТС") or None
    vehicle_model = cf.get("cf_АТС") or None
    return CreateGpsUnitDTO(
        argus_id=unit.id,
        name=unit.name[:64],
        plate_normalized=(canonical_plate(unit.name) or "")[:32] or None,
        imei=(unit.hw_id or cf.get("cf_IMEI") or "")[:32] or None,
        hw_type=(unit.hw_type or "")[:64] or None,
        equipment=cf.get("cf_Тип оборудования") or None,
        group_name=cf.get("cf_Имя группы") or None,
        vehicle_type=vehicle_type,
        vehicle_model=vehicle_model,
        is_lift=is_lift(vehicle_type, vehicle_model),
        cf=cf or None,
        sensors=unit.sensors or None,
        last_message_at=(
            from_unix(unit.last_message.time) if unit.last_message is not None else None
        ),
    )


def tracked_unit_ids(
    units: Sequence[tuple[int, str | None, bool]],
    wanted: set[str],
) -> set[int]:
    """Какие машины грузить: подъёмники и машины с номером из ``wanted``.

    ``units`` — ``(id, plate_normalized, is_lift)``.
    """
    return {
        unit_id
        for unit_id, plate, lift in units
        if lift or (plate is not None and plate in wanted)
    }
