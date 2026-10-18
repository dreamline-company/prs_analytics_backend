"""Разбор ответов Аргуса в строки gps_unit / gps_message."""

from datetime import datetime

from apps.gps.services.parsing import (
    from_unix,
    ignition,
    is_lift,
    message_dto,
    message_dtos,
    parse_params,
    to_unix,
    tracked_unit_ids,
    unit_dto,
)
from shared.integrations.argus.api import ArgusMessageDto, ArgusUnitDto


def local(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    """Местное naive-время октября 2026 — как хранится в БД."""
    return datetime(2026, 10, day, hour, minute, second)  # noqa: DTZ001


# 14.10.2026 10:02:30 по Атырау (пример из docs/argus_api.md).
TS = 1791954150
NOW = local(14, 12, 0)


def _message(**overrides: object) -> ArgusMessageDto:
    payload = {
        "time": TS,
        "lat": "47.72376333",
        "lon": "54.153755",
        "speed": 0,
        "course": 134,
        "height": 104,
        "sats": 18,
        "params": "ign:0;ext_voltage:25.6600;rcv_tm:1791954155;cntM:1650.484;",
    }
    payload.update(overrides)
    message = ArgusMessageDto.from_payload(payload)
    assert message is not None
    return message


def test_unix_round_trip_in_local_time() -> None:
    assert from_unix(TS) == local(14, 10, 2, 30)
    assert to_unix(local(14, 10, 2, 30)) == TS


def test_params_and_ignition_per_tracker() -> None:
    assert parse_params("ign:1;ext_voltage:26.28;") == {
        "ign": "1",
        "ext_voltage": "26.28",
    }
    assert parse_params(None) == {}
    assert ignition({"ign": "0"}) is False
    assert ignition({"ign": "1"}) is True
    assert ignition({"io_239": "1", "io_240": "0"}) is True  # Teltonika
    assert ignition({"ext_voltage": "25.1"}) is None


def test_message_dto_from_doc_sample() -> None:
    dto = message_dto(7, _message(), now=NOW)

    assert dto is not None
    assert dto.unit_id == 7
    assert dto.time == local(14, 10, 2, 30)
    assert (dto.lat, dto.lon) == (47.72376333, 54.153755)
    assert dto.ignition is False
    assert dto.received_at == local(14, 10, 2, 35)


def test_message_without_fix_or_from_future_is_dropped() -> None:
    assert message_dto(7, _message(sats=0), now=NOW) is None
    assert message_dto(7, _message(lat="0", lon="0"), now=NOW) is None
    assert message_dto(7, _message(lat=""), now=NOW) is None
    future = to_unix(local(14, 12, 30))
    assert message_dto(7, _message(time=future), now=NOW) is None


def test_out_of_range_smallint_becomes_null() -> None:
    dto = message_dto(7, _message(height=70000), now=NOW)
    assert dto is not None
    assert dto.height is None


def test_message_dtos_keep_one_point_per_second() -> None:
    dtos = message_dtos(
        7,
        [_message(), _message(speed=5), _message(time=TS + 60), _message(sats=0)],
        now=NOW,
    )
    assert [(dto.time, dto.speed) for dto in dtos] == [
        (local(14, 10, 2, 30), 0),
        (local(14, 10, 3, 30), 0),
    ]


def test_is_lift_by_vehicle_type_or_model() -> None:
    assert is_lift("ПОДЪЕМНИК АПРС - 40", None)
    assert is_lift(None, "УРАЛ 4320-1951-72 АПРС-40")
    assert is_lift("Подъёмник ПТП-40", None)
    assert is_lift("ПАП-80", None)
    assert not is_lift("АВТОЦИСТЕРНА", "КАМАЗ 43118")
    assert not is_lift(None, None)


def test_unit_dto_from_full_get_units_item() -> None:
    unit = ArgusUnitDto.from_payload(
        {
            "id": 243,
            "name": "730AU06",
            "hw_id": "860103062961850",
            "hw_type": "wialon_ips",
            "cf": {
                "cf_Имя группы": 'НГДУ "Кайнармунайгаз"',
                "cf_Тип АТС": "ПОДЪЕМНИК АПРС - 40",
                "cf_АТС": "УРАЛ 4320-1951-72 АПРС-40",
                "cf_Тип оборудования": "Вега MT X CAN",
            },
            "lmsg": {"time": TS, "lat": "47.7", "lon": "54.1"},
            "sensors": [{"n": "CAN - Моточасы"}],
        },
    )
    assert unit is not None

    dto = unit_dto(unit)

    assert dto.argus_id == 243
    assert dto.plate_normalized == "730AU06"
    assert dto.imei == "860103062961850"
    assert dto.group_name == 'НГДУ "Кайнармунайгаз"'
    assert dto.equipment == "Вега MT X CAN"
    assert dto.is_lift is True
    assert dto.last_message_at == local(14, 10, 2, 30)
    assert dto.sensors == [{"n": "CAN - Моточасы"}]


def test_short_get_units_item_without_cf() -> None:
    # PHP отдаёт пустую карточку как [] — это не ошибка.
    unit = ArgusUnitDto.from_payload({"id": 579, "name": "010IZO06", "cf": []})
    assert unit is not None
    dto = unit_dto(unit)
    assert dto.cf is None
    assert dto.is_lift is False
    assert dto.last_message_at is None
    assert ArgusUnitDto.from_payload({"id": 1, "name": ""}) is None


def test_tracked_units_are_lifts_and_known_vehicles() -> None:
    units = [(1, "730AU06", True), (2, "720AU06", False), (3, "999ZZ06", False)]
    assert tracked_unit_ids(units, {"720AU06"}) == {1, 2}
    assert tracked_unit_ids(units, set()) == {1}
