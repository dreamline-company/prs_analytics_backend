"""Пересчёт регистров СДМО по koef справочника: тип станции, округление, флаг."""

from types import SimpleNamespace

from apps.telemetry.services.sdmo_scale import (
    PUMP_PARAMETER_REGISTERS,
    SdmoRegisterScaler,
    apply_koef,
    decimals_for,
)


def _reg(
    type_1900: int | None,
    addr: int,
    koef: float | None,
    units: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(type_1900=type_1900, addr=addr, koef=koef, units=units)


# Справочник как на проде: 1998 у ШГН ×0.1 ход/мин, у ЭВН ×1 об/мин; 1614 ×0.01.
_SCALER = SdmoRegisterScaler(
    [
        _reg(1, 1991, 1.0),
        _reg(1, 1998, 0.1, "ход/мин"),
        _reg(1, 1997, 1.0, "%"),
        _reg(1, 1614, 0.01, "А"),
        _reg(6, 1991, 1.0),
        _reg(6, 1998, 1.0, "об/мин"),
        _reg(6, 1997, 1.0, "%"),
        _reg(None, 4361, 0.1),  # строка без типа в справочник не попадает
    ],
)


def test_decimals_follow_koef() -> None:
    assert decimals_for(1) == 0
    assert decimals_for(0.1) == 1
    assert decimals_for(0.01) == 2
    assert decimals_for(0.0001) == 4


def test_apply_koef_rounds_binary_tails() -> None:
    assert apply_koef(127, 0.1) == 12.7
    assert apply_koef(1234, 0.01) == 12.34
    assert apply_koef(48.37, 1) == 48.37


def test_scale_uses_station_type() -> None:
    assert _SCALER.scale(127, addr=1998, type_1900=1) == 12.7
    assert _SCALER.scale(127, addr=1998, type_1900=6) == 127
    assert _SCALER.scale(None, addr=1998, type_1900=1) is None
    assert _SCALER.units(addr=1998, type_1900=1) == "ход/мин"
    assert _SCALER.units(addr=1998, type_1900=6) == "об/мин"


def test_unknown_type_returns_raw_and_unscaled() -> None:
    row = {"pump_moment": 48.0, "pump_speed": 127.0, "pump_fill": 80.0}
    scaled = _SCALER.scale_row(row, type_1900=None, registers=PUMP_PARAMETER_REGISTERS)
    assert scaled.scaled is False
    assert scaled.values == row
    assert _SCALER.units(addr=1998, type_1900=None) is None
    # Строки справочника без type_1900 не используются даже по адресу.
    assert _SCALER.koef(addr=4361, type_1900=None) is None


def test_scale_row_shgn_and_missing_register() -> None:
    row = {"pump_moment": 48.0, "pump_speed": 127.0, "pump_fill": None}
    scaled = _SCALER.scale_row(row, type_1900=1, registers=PUMP_PARAMETER_REGISTERS)
    assert scaled.scaled is True
    assert scaled.values == {"pump_moment": 48.0, "pump_speed": 12.7, "pump_fill": None}
    # Тип известен, но регистра для него в справочнике нет — сырое и флаг снят.
    partial = _SCALER.scale_row(
        {"pump_moment": 48.0, "pump_speed": 127.0, "pump_fill": 80.0},
        type_1900=6,
        registers={**PUMP_PARAMETER_REGISTERS, "engine_current": 1614},
    )
    assert partial.scaled is False
    assert partial.values["pump_speed"] == 127.0
