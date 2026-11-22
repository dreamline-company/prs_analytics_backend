"""Скважина: остановка и пуск привода (``well.drive_stopped`` / ``well.drive_started``).

Цепочка — строка остановки ``wells_well_state``: источник ``SOURCE_WELL_STOP``
и id строки. Каждая остановка — своя цепочка из двух уведомлений.

Решения пользователя (каталог ``docs/notifications_events.md``):

- остановка шлётся сразу, без порога длительности, и только если в ABAI
  скважина «В работе» (WRK): простой в ABAI — уже известная остановка;
- шлётся только новая строка, открытая заменой другой (переход replace).
  Первая строка скважины (open) и переписывание на месте (rewrite, update)
  не шлются;
- пуск шлётся, когда такую остановку закрыла замена, а в новом решении
  привод работает.

Пуск — только кандидат: строку остановки правят на месте (в ABAI поставили
простой, остановка переписана в ремонт), и её входы на момент пуска уже не
те, что при остановке. Поэтому кандидат — любая замена строки «остановлена»
или «ремонт» работающим приводом, а пишет его производитель, только если по
закрытой строке в журнале действительно есть ``well.drive_stopped``
(``stop_dedup_key``): нет остановки — нет и одинокого пуска.

Модуль только строит DTO; пишет их ``WellStateRefresher`` перед ``commit``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from apps.notifications.constants import SEVERITY_INFO, SEVERITY_WARNING
from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationFactDTO,
)
from apps.notifications.producers.common import (
    SOURCE_WELL_STOP,
    dedup_key,
    fmt_dt,
    well_title,
)
from apps.wells.models.well_state import STATE_REPAIR, STATE_STOPPED
from apps.wells.services.well_state.rules import ABAI_WORK

EVENT_DRIVE_STOPPED = "well.drive_stopped"
EVENT_DRIVE_STARTED = "well.drive_started"

# Причина «привод не работает по СДМО» (rules.decide).
REASON_DRIVE_OFF = "drive_off"
# Строки, чьё закрытие работающим приводом — кандидат в пуск: остановку
# могли переписать на месте в ремонт.
START_AFTER_STATES = frozenset({STATE_STOPPED, STATE_REPAIR})


class StateRow(Protocol):
    """Строка журнала состояний: ``OpenState`` прогона подходит как есть."""

    @property
    def id(self) -> int: ...
    @property
    def state(self) -> str: ...
    @property
    def reason_code(self) -> str: ...
    @property
    def started_at(self) -> datetime: ...
    @property
    def evidence(self) -> Mapping[str, Any] | None: ...


def fmt_duration(span: timedelta) -> str:
    """«25 мин», «1 ч 5 мин», «2 сут 3 ч»; отрицательное — «0 мин»."""
    minutes = max(int(span.total_seconds() // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} сут {hours} ч" if hours else f"{days} сут"
    if hours:
        return f"{hours} ч {mins} мин" if mins else f"{hours} ч"
    return f"{mins} мин"


def _section(evidence: Mapping[str, Any] | None, key: str) -> Mapping[str, Any]:
    value = (evidence or {}).get(key)
    return value if isinstance(value, Mapping) else {}


def parse_moment(value: object) -> datetime | None:
    """Время из ``evidence`` / ``payload`` (ISO-строка); иное — ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_reported_stop(row: StateRow) -> bool:
    """Остановка, о которой шлём: привод встал по СДМО, а в ABAI «В работе».

    Статус ABAI берётся из ``evidence`` строки — входов на момент последней
    записи строки (при открытии или правке на месте).
    """
    return (
        row.state == STATE_STOPPED
        and row.reason_code == REASON_DRIVE_OFF
        and _section(row.evidence, "abai").get("code") in ABAI_WORK
    )


def is_drive_running(evidence: Mapping[str, Any] | None) -> bool:
    """В решении привод работает: свежее подтверждённое «работает» по СДМО."""
    drive = _section(evidence, "drive")
    return drive.get("running") is True and drive.get("known") is True


def drive_stopped_notification(
    *,
    well_id: int,
    well_name: str | None,
    abai_ngdu_id: int | None,
    row: StateRow,
) -> NotificationCreateDTO:
    """``row`` — новая строка остановки."""
    abai = _section(row.evidence, "abai")
    drive = _section(row.evidence, "drive")
    status = abai.get("status") or abai.get("code") or "—"
    facts = [
        NotificationFactDTO(label_ru="Стоит с", value_ru=fmt_dt(row.started_at)),
        NotificationFactDTO(label_ru="Статус ABAI", value_ru=str(status)),
    ]
    last_sample = parse_moment(drive.get("last_sample_at"))
    if last_sample is not None:
        facts.append(
            NotificationFactDTO(
                label_ru="Последний отсчёт СДМО",
                value_ru=fmt_dt(last_sample),
            ),
        )
    return NotificationCreateDTO(
        event_code=EVENT_DRIVE_STOPPED,
        severity=SEVERITY_WARNING,
        title_ru=well_title(well_name, "привод остановлен"),
        body_ru=(
            f"Привод не работает по СДМО с {fmt_dt(row.started_at)}, "
            f"в ABAI скважина «{status}»."
        ),
        well_id=well_id,
        abai_ngdu_id=abai_ngdu_id,
        source_type=SOURCE_WELL_STOP,
        source_id=row.id,
        dedup_key=stop_dedup_key(row.id),
        payload={
            "well_state_id": row.id,
            "abai_status_code": abai.get("code"),
            "station_id": drive.get("station_id"),
            "drive_since": drive.get("since"),
        },
        facts=facts,
        event_at=row.started_at,
    )


def drive_started_notification(
    *,
    well_id: int,
    well_name: str | None,
    abai_ngdu_id: int | None,
    stop: StateRow,
    ended_at: datetime,
) -> NotificationCreateDTO:
    """``stop`` — закрытая строка остановки, ``ended_at`` — её окончание."""
    stood = fmt_duration(ended_at - stop.started_at)
    return NotificationCreateDTO(
        event_code=EVENT_DRIVE_STARTED,
        severity=SEVERITY_INFO,
        title_ru=well_title(well_name, "привод снова работает"),
        body_ru=(
            f"Привод стоял {stood}: с {fmt_dt(stop.started_at)} до {fmt_dt(ended_at)}."
        ),
        well_id=well_id,
        abai_ngdu_id=abai_ngdu_id,
        source_type=SOURCE_WELL_STOP,
        source_id=stop.id,
        dedup_key=dedup_key(EVENT_DRIVE_STARTED, SOURCE_WELL_STOP, stop.id),
        payload={
            "well_state_id": stop.id,
            "stopped_at": stop.started_at.isoformat(),
            "started_at": ended_at.isoformat(),
            "stopped_minutes": max(
                int((ended_at - stop.started_at).total_seconds() // 60),
                0,
            ),
        },
        facts=[
            NotificationFactDTO(label_ru="Стоял с", value_ru=fmt_dt(stop.started_at)),
            NotificationFactDTO(label_ru="Работает с", value_ru=fmt_dt(ended_at)),
            NotificationFactDTO(label_ru="Простой", value_ru=stood),
        ],
        event_at=ended_at,
    )


def stop_dedup_key(row_id: int) -> str:
    """Ключ остановки строки — по нему кандидат в пуск ищется в журнале."""
    return dedup_key(EVENT_DRIVE_STOPPED, SOURCE_WELL_STOP, row_id)


@dataclass(frozen=True, slots=True)
class ReplaceNotices:
    """Уведомления одной замены строки.

    ``start_candidate`` пишется, только если в журнале (или раньше в той же
    пачке) уже записан ``stop_dedup_key(start_candidate.source_id)``.
    """

    start_candidate: NotificationCreateDTO | None = None
    stopped: NotificationCreateDTO | None = None


def replace_notifications(
    *,
    well_id: int,
    well_name: str | None,
    abai_ngdu_id: int | None,
    closed: StateRow,
    opened: StateRow,
) -> ReplaceNotices:
    """Переход replace: строку ``closed`` закрыла новая ``opened``.

    Начало новой строки — окончание закрытой. Сначала пуск (закрылась
    остановка), потом остановка (открылась новая): так они и случились.
    """
    start_candidate = stopped = None
    if closed.state in START_AFTER_STATES and is_drive_running(opened.evidence):
        start_candidate = drive_started_notification(
            well_id=well_id,
            well_name=well_name,
            abai_ngdu_id=abai_ngdu_id,
            stop=closed,
            ended_at=opened.started_at,
        )
    if is_reported_stop(opened):
        stopped = drive_stopped_notification(
            well_id=well_id,
            well_name=well_name,
            abai_ngdu_id=abai_ngdu_id,
            row=opened,
        )
    return ReplaceNotices(start_candidate=start_candidate, stopped=stopped)
