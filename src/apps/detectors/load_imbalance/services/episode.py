"""State machine эпизода R9: чистая логика переходов, без I/O.

Вход — вердикты по суткам (весь контекст, включая сутки до курсора) и позиция
курсора; действия выдаются только по суткам после курсора, но счётчики серий
прогреваются и на прошлом — серия чистых суток может пересекать границу
прогона.

Кластеризацию alert-суток из скрипта (разрыв до 3 суток — всё ещё один эпизод)
здесь заменяет гистерезис закрытия: эпизод живёт, пока не подтвердилось
восстановление. Это строго консервативнее — закрыться раньше, чем скрипт
разорвал бы кластер, мы не можем.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from apps.detectors.load_imbalance import incident_config
from apps.detectors.load_imbalance.dto.internal.day import (
    STATE_ALERT,
    STATE_CLEAN,
    STATE_UNDETERMINED,
    DayVerdict,
)
from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)

ACTION_OPEN_WARNING = "open_warning"
ACTION_ESCALATE = "escalate"
ACTION_CONFIRM = "confirm"
ACTION_NORMALIZE = "normalize"


@dataclass(frozen=True, slots=True)
class EpisodeAction:
    """Одно решение state machine.

    Attributes:
        kind: Тип действия (ACTION_*).
        day: Сутки, на которых оно принято: для open — первые alert-сутки
            эпизода (opened_at), для normalize — сутки, замкнувшие серию
            восстановления.
    """

    kind: str
    day: date


def evaluate_episode(
    verdicts: Sequence[DayVerdict],
    *,
    cursor_day: date | None,
    active_level: str | None,
) -> list[EpisodeAction]:
    """Прогнать вердикты по суткам через жизненный цикл эпизода.

    Args:
        verdicts: Вердикты правила (контекст + новые сутки), любой порядок.
        cursor_day: Последние уже обработанные сутки; None — первый прогон.
        active_level: Уровень активного эпизода в БД (warning/alarm) или None.

    Returns:
        Действия в хронологическом порядке. CONFIRM схлопнут до последнего.
    """
    ordered = sorted(verdicts, key=lambda verdict: verdict.day)

    level = active_level
    # Сколько alert-суток уже набрал открытый эпизод. Восстанавливается из
    # уровня, а не пересчётом истории: warning по построению означает ровно
    # одни alert-сутки, alarm — что порог эскалации уже взят.
    alert_days = _seed_alert_days(active_level)
    clean_run = 0
    actions: list[EpisodeAction] = []
    last_confirm: EpisodeAction | None = None

    for verdict in ordered:
        # Сутки без вердикта (мало снимков, не хватило фона) серию чистых суток
        # не продолжают, но и не обнуляют: отсутствие данных — не рецидив.
        # Затянувшееся отсутствие закрывает эпизод отдельно, по STALE_DAYS.
        if verdict.state == STATE_CLEAN:
            clean_run += 1
        elif verdict.state != STATE_UNDETERMINED:
            clean_run = 0

        # Сутки до курсора только прогревают серию чистых суток: alert-сутки
        # прошлого уже учтены в active_level.
        if cursor_day is not None and verdict.day <= cursor_day:
            continue

        if verdict.state == STATE_ALERT:
            alert_days += 1
            clean_run = 0
            action, level = _on_alert_day(level, alert_days, verdict.day)
            if action.kind == ACTION_CONFIRM:
                last_confirm = action
            else:
                actions.append(action)
                last_confirm = None
            continue

        if level is None:
            continue

        if clean_run >= incident_config.RECOVER_SUSTAIN_DAYS:
            actions.append(EpisodeAction(ACTION_NORMALIZE, verdict.day))
            level = None
            alert_days = 0
            clean_run = 0
            last_confirm = None

    if last_confirm is not None:
        actions.append(last_confirm)

    return actions


def _seed_alert_days(active_level: str | None) -> int:
    if active_level == INCIDENT_LEVEL_ALARM:
        return incident_config.ALARM_ALERT_DAYS
    if active_level == INCIDENT_LEVEL_WARNING:
        return 1
    return 0


def _on_alert_day(
    level: str | None,
    alert_days: int,
    day: date,
) -> tuple[EpisodeAction, str]:
    """Переход эпизода на alert-сутках: открыть, эскалировать или подтвердить."""
    if level is None:
        return EpisodeAction(ACTION_OPEN_WARNING, day), INCIDENT_LEVEL_WARNING
    if (
        level == INCIDENT_LEVEL_WARNING
        and alert_days >= incident_config.ALARM_ALERT_DAYS
    ):
        return EpisodeAction(ACTION_ESCALATE, day), INCIDENT_LEVEL_ALARM
    return EpisodeAction(ACTION_CONFIRM, day), level
