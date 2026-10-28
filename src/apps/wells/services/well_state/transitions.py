"""Как решение ложится в журнал состояний — чистая логика, без базы.

1. Новая строка открывается только при смене ``state``. Смена источника или
   причины при том же ``state`` обновляет открытую строку на месте.
2. Начало нового состояния — по данным (``Decision.started_at``), не позже
   «сейчас»; неизвестно — «сейчас».
3. Без наложений: открытая строка закрывается моментом начала новой.
   - Начало по данным позже начала открытой строки — с него.
   - Совпадает — строка переписывается на месте. Так же переписывается
     «остановлена» в «ремонт»: ремонт приходит из ABAI раз в сутки, и это то
     же событие, увиденное позже.
   - Раньше — условие нового состояния выполнялось и до открытой строки, а
     сменилось что-то другое (например, привод встал посреди ремонта): смена
     засчитывается в момент, когда её увидели. Иначе переписались бы реальные
     периоды (ремонт → привод запустили → привод встал).
"""

from dataclasses import dataclass
from datetime import datetime

from apps.wells.models.well_state import STATE_REPAIR, STATE_STOPPED
from apps.wells.services.well_state.rules import Decision

KIND_NONE = "none"  # ничего не менялось
KIND_UPDATE = "update"  # тот же state, другая причина — на месте
KIND_REWRITE = "rewrite"  # другой state с началом открытой строки — на месте
KIND_REPLACE = "replace"  # закрыть открытую строку и открыть новую
KIND_OPEN = "open"  # открытой строки не было


@dataclass(frozen=True, slots=True)
class OpenState:
    """Снимок открытой строки журнала."""

    id: int
    state: str
    source: str
    reason_code: str
    reason_ru: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class Transition:
    kind: str
    started_at: datetime | None = None  # для open / replace / rewrite


def _is_rename(open_state: OpenState, decision: Decision) -> bool:
    """Остановка, за которой утром пришёл ремонт, — то же событие: и по
    статусу ABAI, и по СДМО (ABAI округляет время до минуты, и ремонт с
    16:19:00 начинается раньше остановки привода в 16:19:19)."""
    return open_state.state == STATE_STOPPED and decision.state == STATE_REPAIR


def plan_transition(
    open_state: OpenState | None,
    decision: Decision,
    now: datetime,
) -> Transition:
    """``now`` — момент, на который принято решение (время прогона или смены
    привода внутри прогона)."""
    data_start = (
        min(decision.started_at, now) if decision.started_at is not None else None
    )
    if open_state is None:
        return Transition(KIND_OPEN, data_start or now)
    if open_state.state == decision.state:
        same = (open_state.source, open_state.reason_code, open_state.reason_ru) == (
            decision.source,
            decision.reason_code,
            decision.reason_ru,
        )
        return Transition(KIND_NONE if same else KIND_UPDATE)

    if data_start is not None and data_start > open_state.started_at:
        return Transition(KIND_REPLACE, data_start)
    if data_start == open_state.started_at or _is_rename(open_state, decision):
        return Transition(KIND_REWRITE, open_state.started_at)
    if now > open_state.started_at:
        return Transition(KIND_REPLACE, now)
    return Transition(KIND_REWRITE, open_state.started_at)
