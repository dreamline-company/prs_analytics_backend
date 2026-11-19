"""Работает ли привод по отсчёту СДМО — одна классификация на всю систему.

Основной сигнал — регистр 1999 «Статус (VLT)»: код состояния привода, а не
«станция на связи». Наборы кодов сверены на проде 21–28.10.2026 по току и
оборотам: при «рабочих» кодах ток почти в 100 % отсчётов (при 8, 32, 40, 63 и
обороты > 0 везде), при кодах остановки обороты почти всегда 0. Коды 64–511
не встречались — неизвестный код решается по оборотам.

Регистр 1999 пуст в 70 % отсчётов ЖМГ и 15 % КМГ (у станций типа 12 его нет
вовсе), поэтому для отсчёта без статуса берутся обороты (регистр 1998). Ток
(1614) не используется: при остановке он больше нуля в 9–50 % отсчётов.

Проверка тревог R2 держит свой набор кодов (``rod_breaks/verification``) —
у неё своя версия правила.

``vlt_status_kind`` — разбор того же кода по битам (таблица РЭ VFD-OSA,
параметр 19-99 DRIVE STATUS): для отрезков состояния привода на графике.
Состояние скважины и сводки пока живут на явных наборах кодов выше.
"""

import math
from enum import StrEnum

DRIVE_RUNNING_VLT_CODES = frozenset({1, 2, 4, 8, 16, 32, 40, 48, 55, 63})
DRIVE_STOPPED_VLT_CODES = frozenset(
    {0, -1, 512, 1024, 4096, 8192, 9999, 16384, 1048576, 134250496},
)


def _finite(value: float | None) -> float | None:
    """NaN и бесконечность в регистрах — мусор источника, как пустое значение."""
    if value is None or not math.isfinite(value):
        return None
    return value


def vlt_code(value: float | None) -> int | None:
    """Код статуса как целое: ``r_1999`` хранится как REAL."""
    value = _finite(value)
    if value is None:
        return None
    return round(value)


def vlt_running(value: float | None) -> bool | None:
    """По одному коду статуса: ``True`` / ``False``; ``None`` — кода нет или он
    неизвестен."""
    code = vlt_code(value)
    if code in DRIVE_RUNNING_VLT_CODES:
        return True
    if code in DRIVE_STOPPED_VLT_CODES:
        return False
    return None


def is_known_vlt(value: float | None) -> bool:
    code = vlt_code(value)
    return code in DRIVE_RUNNING_VLT_CODES or code in DRIVE_STOPPED_VLT_CODES


def drive_running(vlt: float | None, rpm: float | None) -> bool | None:
    """Работает ли привод по отсчёту: статус, а без него (или при неизвестном
    коде) — обороты. ``None`` — отсчёт непригоден (нет ни того, ни другого)."""
    by_status = vlt_running(vlt)
    if by_status is not None:
        return by_status
    rpm = _finite(rpm)
    if rpm is None:
        return None
    return rpm > 0


# --- Разбор кода 1999 по битам ---------------------------------------------
#
# Код — битовая маска: каждый код РЭ — степень двойки. На проде (18.05–18.11.2026,
# 68,6 млн отсчётов) 99,99 % значений — одиночные коды, суммы редки, но
# раскладываются осмысленно (3 = 1 + 2, 40 = 8 + 32, 63…). Предупреждения 8 / 32 /
# 256 приходят БЕЗ бита работы, хотя привод вращается (частота > 0 почти всегда):
# поэтому бит предупреждения тоже значит «работает».

VLT_RUN_BITS = 1 | 2 | 4
VLT_WARNING_BITS = 8 | 16 | 32 | 64 | 256
VLT_STATE_BITS = 128 | 16384 | 1048576
VLT_ALARM_BITS = 512 | 1024 | 4096 | 8192 | 32768 | 65536 | 262144 | 524288
VLT_KNOWN_BITS = VLT_RUN_BITS | VLT_WARNING_BITS | VLT_STATE_BITS | VLT_ALARM_BITS

# Заполнитель источника: все его биты есть в таблице, но смысла сумма не несёт
# (авто, ручной, накопление, два предупреждения и три аварии сразу), частота при
# нём всегда 0. Прочий
# мусор (−1, 9999999, 134250496) отсекается знаком и неизвестными битами.
VLT_FILLER_CODES = frozenset({9999})


class VltStatusKind(StrEnum):
    RUN = "run"
    STOP = "stop"
    ALARM = "alarm"


def vlt_status_kind(value: float | None) -> VltStatusKind | None:
    """Состояние привода по битам кода 1999; ``None`` — кода нет или он мусор.

    Есть аварийный бит — авария; иначе бит работы или предупреждения — работает;
    иначе (0, автоперезапуск 16384, задержка перезапуска 1048576, продувка 128) —
    стоит. Дробное значение (REAL хранит целые коды точно) — тоже мусор.
    """
    value = _finite(value)
    if value is None or not float(value).is_integer():
        return None
    code = int(value)
    if code < 0 or code in VLT_FILLER_CODES or code & ~VLT_KNOWN_BITS:
        return None
    if code & VLT_ALARM_BITS:
        return VltStatusKind.ALARM
    if code & (VLT_RUN_BITS | VLT_WARNING_BITS):
        return VltStatusKind.RUN
    return VltStatusKind.STOP
