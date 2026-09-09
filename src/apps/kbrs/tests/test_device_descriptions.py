"""Справочник приборов Toucan → описания для ``kbrs_measure``."""

from apps.kbrs.tasks.poll_measures.poller import device_description_map
from shared.integrations.kbrs.api.dtos import DeviceDto


def _device(device_id: str, owner_id: int, description: str) -> DeviceDto:
    return DeviceDto(
        device_id=device_id,
        owner_id=owner_id,
        directory_id=0,
        dirtype=0,
        description=description,
    )


def test_device_description_map_keys_by_owner_and_int_device_id() -> None:
    devices = [
        _device("101", 4, " ДЭЛ-150 №7 "),
        _device("102", 4, ""),
        _device("abc", 4, "нечисловой id"),
        _device("101", 2, "тот же номер у другого НГДУ"),
    ]

    result = device_description_map(devices)

    assert result == {
        (4, 101): "ДЭЛ-150 №7",
        (2, 101): "тот же номер у другого НГДУ",
    }
