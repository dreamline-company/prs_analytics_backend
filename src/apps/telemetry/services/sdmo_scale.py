"""Пересчёт сырых регистров СДМО в физические единицы по справочнику fc_reg.

В ``telemetry_sdmo_fc_data`` значения лежат как в источнике; ``koef`` из
``telemetry_sdmo_fc_reg`` с ключом ``(type_1900, addr)`` применяется только на
выдаче. Тип станции обязателен: у 13 регистров koef расходится по типам
(скорость 1998 у ШГН ×0.1 в ход/мин, у ЭВН ×1 в об/мин). Без ``type_1900``
у станции значения отдаются сырыми с ``scaled=False`` — решение владельца,
догадок по «похожим типам» не делаем.

Детекторы R2/R9 читают регистры сами и сюда не ходят: их пороги
откалиброваны на сырых значениях, а отношения от koef не зависят.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Self

from apps.telemetry.repositories.sdmo import (
    ENGINE_CURRENT_REGISTER,
    PUMP_FILL_REGISTER,
    PUMP_MOMENT_REGISTER,
    ROTOR_SPEED_REGISTER,
    SdmoFcRegRepository,
)

# Поле выборки -> адрес регистра: последний отсчёт насоса (карточка, чат).
PUMP_PARAMETER_REGISTERS: Mapping[str, int] = {
    "pump_moment": PUMP_MOMENT_REGISTER,
    "pump_speed": ROTOR_SPEED_REGISTER,
    "pump_fill": PUMP_FILL_REGISTER,
}
# То же для ряда параметров (/telemetry/v1/sdmo-parameters).
SERIES_PARAMETER_REGISTERS: Mapping[str, int] = {
    "rotor_speed": ROTOR_SPEED_REGISTER,
    "pump_moment": PUMP_MOMENT_REGISTER,
    "engine_current": ENGINE_CURRENT_REGISTER,
}


class RegisterLike(Protocol):
    """Строка справочника регистров (ORM-модель или заглушка в тестах)."""

    type_1900: int | None
    addr: int
    koef: float | None
    units: str | None


@dataclass(frozen=True, slots=True)
class ScaledRow:
    values: dict[str, float | None]
    # True — у станции известен тип и для каждого поля нашёлся koef.
    scaled: bool


def decimals_for(koef: float) -> int:
    """Знаков после запятой у koef: 0.1 -> 1, 0.01 -> 2, 1 -> 0.

    Округление результата до разрядности коэффициента убирает хвосты
    двоичной арифметики (127 × 0.1 = 12.700000000000001).
    """
    exponent = Decimal(str(koef)).normalize().as_tuple().exponent
    return max(-exponent, 0) if isinstance(exponent, int) else 0


def apply_koef(value: float, koef: float) -> float:
    if koef == 1:
        return value
    return round(value * koef, decimals_for(koef))


class SdmoRegisterScaler:
    def __init__(self, registers: Iterable[RegisterLike]) -> None:
        self._registers = {
            (register.type_1900, register.addr): register
            for register in registers
            if register.type_1900 is not None
        }

    @classmethod
    async def load(cls, repository: SdmoFcRegRepository) -> Self:
        """Весь справочник одним запросом — на порядок сотен строк."""
        return cls(await repository.list_all())

    def koef(self, *, addr: int, type_1900: int | None) -> float | None:
        """None — тип неизвестен или регистра нет в справочнике для этого типа."""
        if type_1900 is None:
            return None
        register = self._registers.get((type_1900, addr))
        return register.koef if register is not None else None

    def units(self, *, addr: int, type_1900: int | None) -> str | None:
        if type_1900 is None:
            return None
        register = self._registers.get((type_1900, addr))
        return (register.units or None) if register is not None else None

    def scale(
        self,
        value: float | None,
        *,
        addr: int,
        type_1900: int | None,
    ) -> float | None:
        """Приведённое значение; без koef — сырое как есть."""
        if value is None:
            return None
        koef = self.koef(addr=addr, type_1900=type_1900)
        return value if koef is None else apply_koef(value, koef)

    def scale_row(
        self,
        row: Mapping[str, object],
        *,
        type_1900: int | None,
        registers: Mapping[str, int],
    ) -> ScaledRow:
        """Пересчитать поля строки выборки по карте «поле -> регистр».

        Флаг честный: хватило одного поля без koef — ``scaled=False``, потому
        что потребитель не отличит, какое из чисел приведено, а какое нет.
        """
        values: dict[str, float | None] = {}
        scaled = type_1900 is not None
        for field, addr in registers.items():
            raw = row.get(field)
            koef = self.koef(addr=addr, type_1900=type_1900)
            if koef is None:
                scaled = False
                values[field] = raw  # type: ignore[assignment]
                continue
            values[field] = None if raw is None else apply_koef(raw, koef)  # type: ignore[arg-type]
        return ScaledRow(values=values, scaled=scaled)
