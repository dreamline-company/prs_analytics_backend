"""Эпизоды детекторов R2, R9, R10 — уведомления ``r2.*``, ``r9.*``, ``r10.*``.

Цепочка — эпизод ``detectors_incident``: источник ``SOURCE_INCIDENT`` и id
эпизода, каждого вида события — одно на эпизод (``dedup_key``). Каталог и
решения пользователя — ``docs/notifications_events.md``.

Раннеры собирают переходы эпизодов за станцию / сутки (``IncidentTransition``
из RETURNING upsert'а и закрытия) и прямо перед своим ``commit`` пишут их
``emit_transitions``; после коммита — ``publish_safely``. Построители ниже
чистые: переход -> ``NotificationCreateDTO``.

Шлют только celery-таски детекторов (beat, диспетчер, подметальщик передают
``notify=True``); ручной запуск ``main`` / CLI не шлёт. И внутри таски — не
всё (``*_notifies``): первый прогон станции или НГДУ без курсора (разбор
истории), явная дата R10, проверка с ``--incident-ids`` / ``--backfill-days`` /
``--recheck-days`` / ``--dry-run`` — нет. Restage уведомлений не пишет.

``event_at``:

- R2 — время данных: начало провала (T0), конец корзины, на которой не
  подтвердился возврат, восстановление момента. Живой прогон идёт по приходу
  СДМО, лаг около часа; догон после молчания станции старше суток эмиттер
  отбрасывает;
- R2, закрытие ремонтом или давностью — момент прогона: их узнают с
  опозданием (станция молчит, ремонт внесли задним числом, ремонт шёл ещё до
  T0), а «Возможный обрыв» без закрытия висел бы на стене. Время закрытия по
  данным — в фактах;
- R2, подтверждение ремонтом — момент проверки;
- R9 и R10 — суточные правила: эпизод начинается с начала суток, и к
  утреннему прогону это всегда старше 24 ч. Поэтому ``event_at`` — момент
  прогона, если событие стало известно за последние сутки (конец расчётных
  суток; у ежечасного R10 и давности R9 — сейчас), а иначе — когда оно стало
  известно: такой догон старых суток эмиттер отбросит. Настоящие даты — в
  фактах.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.cits_events import config as r10_config
from apps.detectors.cits_events import incident_config as r10_incident
from apps.detectors.dto.internal.repositories.incident import IncidentStateDTO
from apps.detectors.labels import closed_label_ru, level_label_ru, lower_first
from apps.detectors.load_imbalance import incident_config as r9_incident
from apps.detectors.models.incident import (
    CLOSE_REASON_MANUAL,
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_REPAIR,
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
)
from apps.detectors.models.verification import VERDICT_CONFIRMED
from apps.detectors.rod_breaks import incident_config as r2_incident
from apps.notifications.constants import (
    MAX_EVENT_AGE,
    SEVERITY_ALARM,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationFactDTO,
)
from apps.notifications.producers.common import (
    SOURCE_INCIDENT,
    dedup_key,
    emit_all,
    fmt_dt,
    well_title,
)
from apps.notifications.services.emitter import notification_emitter
from apps.notifications.services.publisher import publish_notifications
from apps.telemetry.models.sdmo import SdmoStation
from apps.wells.models.well import Well
from core import get_logger

logger = get_logger(__name__)

R2_LOAD_DROP = "r2.load_drop"
R2_POSSIBLE_ROD_BREAK = "r2.possible_rod_break"
R2_CLOSED = "r2.closed"
R2_CONFIRMED = "r2.confirmed"
R9_OPENED = "r9.opened"
R9_ALARM = "r9.alarm"
R9_CLOSED = "r9.closed"
R10_LIQUID_DROP = "r10.liquid_drop"
R10_ZERO_LIQUID = "r10.zero_liquid"
R10_CLOSED = "r10.closed"

# Переходы эпизода в прогоне.
TRANSITION_OPENED = "opened"
TRANSITION_ESCALATED = "escalated"
TRANSITION_CLOSED = "closed"

MAX_FACTS = 5


@dataclass(frozen=True, slots=True)
class IncidentTransition:
    """Переход эпизода в прогоне детектора — вход построителей.

    Attributes:
        kind: ``TRANSITION_*``.
        incident: Эпизод сразу после записи (RETURNING upsert'а / закрытия).
        at: Когда переход случился по данным — см. ``event_at`` в шапке модуля.
        detected_at: Момент прогона, который его увидел (местное время).
        well_name: Имя скважины; пустое досчитает ``emit_transitions``.
        station: Код станции СДМО; пустой досчитает ``emit_transitions`` по
            ``incident.entity_id`` (у R10 станции нет).
        abai_ngdu_id: НГДУ, если раннер его знает (R10); иначе эмиттер найдёт
            по скважине.
    """

    kind: str
    incident: IncidentStateDTO
    at: datetime
    detected_at: datetime
    well_name: str | None = None
    station: str | None = None
    abai_ngdu_id: int | None = None


# --- Шлём / не шлём ---


def live_run_notifies(*, notify: bool, has_cursor: bool) -> bool:
    """Курсорный прогон R2 / R9 станции: шлёт живой прогон.

    Курсора нет — первый прогон станции разбирает историю за окно правила:
    это пересборка, не новость.
    """
    return notify and has_cursor


def r10_run_notifies(*, notify: bool, fix_date: date | None, has_cursor: bool) -> bool:
    """Прогон R10: шлют плановые (06:30, ежечасный, диспетчер). Явная дата —
    ручной пересчёт, без курсора — первый прогон; они не шлют."""
    return notify and fix_date is None and has_cursor


def verify_run_notifies(
    *,
    dry_run: bool,
    incident_ids: Sequence[int] | None,
    backfill_days: int | None,
    recheck_days: int | None,
) -> bool:
    """Проверка R2: шлёт только ежечасный прогон без флагов пересчёта."""
    return (
        not dry_run
        and not incident_ids
        and backfill_days is None
        and recheck_days is None
    )


# Подтверждение — новость, если его улика не старше этого.
CONFIRMATION_MAX_AGE = timedelta(hours=48)
_REPAIR_OPEN_MAX = timedelta(days=r2_incident.REPAIR_OPEN_MAX_DAYS)


def _parse_moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_new_confirmation(  # noqa: PLR0913
    *,
    verdict_before: str | None,
    verdict_after: str,
    written: bool,
    now: datetime,
    evidence_at: datetime | None,
    evidence: Mapping[str, Any] | None = None,
    rule_changed: bool = False,
    max_age: timedelta = CONFIRMATION_MAX_AGE,
) -> bool:
    """Проверка впервые записала «Подтверждено», и это новость.

    Пересборку (после смены правила живой прогон пересчитывает эпизоды за 45
    суток) отсекает свежесть улики: самая поздняя из ``evidence_at`` и начала
    / конца ремонта в ``evidence`` не старше ``max_age``. Ремонт без конца, начатый
    не раньше ``REPAIR_OPEN_MAX_DAYS``, ещё идёт — улика свежая. Дат улики нет
    вовсе — пересчёт отметки старой версии правила не шлёт.
    """
    if not (
        written
        and verdict_after == VERDICT_CONFIRMED
        and verdict_before != VERDICT_CONFIRMED
    ):
        return False
    moments = [evidence_at] if evidence_at is not None else []
    repairs = (evidence or {}).get("repair")
    for repair in repairs if isinstance(repairs, list) else []:
        if not isinstance(repair, Mapping):
            continue
        start = _parse_moment(repair.get("start"))
        end = _parse_moment(repair.get("end"))
        if start is not None and end is None and start >= now - _REPAIR_OPEN_MAX:
            return True
        moments += [moment for moment in (start, end) if moment is not None]
    if not moments:
        return not rule_changed
    return max(moments) >= now - max_age


def discovered_at(
    knowable_at: datetime,
    detected_at: datetime,
    max_age: timedelta = MAX_EVENT_AGE,
) -> datetime:
    """``event_at`` суточных правил: момент прогона, если событие стало
    известно за последние ``max_age``; иначе — когда оно стало известно
    (эмиттер такое отбросит: догон старых суток стену не будит)."""
    if knowable_at >= detected_at - max_age:
        return detected_at
    return knowable_at


# --- Подписи и числа ---


def _num(value: float, digits: int = 0) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _pct(share: float) -> str:
    return f"{share * 100:.0f} %"


def _day(moment: date | datetime) -> str:
    return moment.strftime("%d.%m")


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _fact(label: str, value: str | None) -> NotificationFactDTO | None:
    return NotificationFactDTO(label_ru=label, value_ru=value) if value else None


def _last(items: object) -> Mapping[str, Any]:
    if isinstance(items, list) and items and isinstance(items[-1], Mapping):
        return items[-1]
    return {}


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def _dto(  # noqa: PLR0913
    t: IncidentTransition,
    *,
    event_code: str,
    severity: str,
    text: str,
    body: str,
    facts: Iterable[NotificationFactDTO | None],
    event_at: datetime,
) -> NotificationCreateDTO:
    incident = t.incident
    return NotificationCreateDTO(
        event_code=event_code,
        severity=severity,  # type: ignore[arg-type]
        title_ru=well_title(t.well_name, text),
        body_ru=body,
        well_id=incident.well_id,
        abai_ngdu_id=t.abai_ngdu_id,
        source_type=SOURCE_INCIDENT,
        source_id=incident.id,
        dedup_key=dedup_key(event_code, SOURCE_INCIDENT, incident.id),
        payload={
            "incident_id": incident.id,
            "detector_code": incident.detector_code,
            "level": incident.level,
            "close_reason": incident.close_reason,
        },
        facts=[fact for fact in facts if fact is not None][:MAX_FACTS],
        event_at=event_at,
    )


# --- R2: обрыв штанг ---

_R2_CLOSE_BODIES = {
    CLOSE_REASON_RECOVERED: (
        "Момент вернулся к обычному уровню и держится — сигнал «{stage}» закрыт."
    ),
    CLOSE_REASON_REPAIR: "На скважине начался ремонт — сигнал «{stage}» закрыт.",
    CLOSE_REASON_STALE: (
        f"За {r2_incident.STALE_DAYS} суток момент не восстановился и ремонта "
        "не было — сигнал «{stage}» закрыт по давности."
    ),
    CLOSE_REASON_MANUAL: "Сигнал «{stage}» закрыт вручную.",
}


# Закрытия, которые узнают с опозданием: датируются моментом прогона.
_R2_LATE_CLOSE = frozenset({CLOSE_REASON_REPAIR, CLOSE_REASON_STALE})


def _moment_value(level: float | None, base: float | None) -> str | None:
    """«120 при базе 410 (29 %)»; без базы — только момент."""
    if level is None:
        return None
    if base:
        return f"{_num(level)} при базе {_num(base)} ({_pct(level / base)})"
    return _num(level)


def _r2_facts(t: IncidentTransition) -> list[NotificationFactDTO | None]:
    incident = t.incident
    payload = incident.payload or {}
    last = _last(payload.get("evidence"))
    base = _number(payload.get("base_t0")) or _number(payload.get("base_moment"))
    speed = _number(last.get("spd"))
    facts = [_fact("Начало провала", fmt_dt(incident.opened_at))]
    if t.kind == TRANSITION_CLOSED:
        reason = incident.close_reason or CLOSE_REASON_RECOVERED
        facts += [
            _fact("Закрыт", fmt_dt(incident.normalized_at or t.at)),
            _fact("Причина", closed_label_ru("R2", reason)),
        ]
    else:
        check = payload.get("stage_check")
        if incident.level == INCIDENT_LEVEL_ALARM and isinstance(check, Mapping):
            facts.append(
                _fact(
                    "Момент за последний час",
                    _moment_value(
                        _number(check.get("level")),
                        _number(check.get("base")),
                    )
                    or "мало данных, возврат не доказан",
                ),
            )
        else:
            facts.append(
                _fact(
                    "Момент сейчас",
                    _moment_value(_number(last.get("mom_min")), base),
                ),
            )
        facts.append(_fact("Скорость", f"{_num(speed)} об/мин" if speed else None))
    facts.append(_fact("Станция", t.station))
    return facts


def r2_notification(t: IncidentTransition) -> NotificationCreateDTO:
    """Открытие («Падение нагрузки»), эскалация до «Возможный обрыв штанг»
    (и старое открытие сразу аварией), закрытие."""
    incident = t.incident
    facts = _r2_facts(t)
    if t.kind == TRANSITION_CLOSED:
        reason = incident.close_reason or CLOSE_REASON_RECOVERED
        stage = level_label_ru("R2", incident.level)
        body = _R2_CLOSE_BODIES.get(reason, "Сигнал «{stage}» закрыт.")
        return _dto(
            t,
            event_code=R2_CLOSED,
            severity=SEVERITY_INFO,
            text=lower_first(closed_label_ru("R2", reason)),
            body=body.format(stage=stage),
            facts=facts,
            # Ремонт и давность узнают с опозданием — момент прогона.
            event_at=t.detected_at if reason in _R2_LATE_CLOSE else t.at,
        )
    if incident.level == INCIDENT_LEVEL_ALARM:
        return _dto(
            t,
            event_code=R2_POSSIBLE_ROD_BREAK,
            severity=SEVERITY_ALARM,
            text="возможный обрыв штанг",
            body=(
                f"За {r2_incident.STAGE_ESCALATE_HOURS} ч от начала провала "
                "момент не вернулся к обычному: привод крутит штанги без "
                "нагрузки насоса. Подтвердить обрыв может только ПРС."
            ),
            facts=facts,
            event_at=t.at,
        )
    return _dto(
        t,
        event_code=R2_LOAD_DROP,
        severity=SEVERITY_WARNING,
        text="падение нагрузки",
        body=(
            "Момент на штангах упал ниже "
            f"{_pct(r2_incident.WARN_MOM_DROP_RATIO)} обычного, привод при этом "
            f"крутится. Если за {r2_incident.STAGE_ESCALATE_HOURS} ч от начала "
            "провала момент не вернётся, сигнал станет «Возможный обрыв штанг»."
        ),
        facts=facts,
        event_at=t.at,
    )


def r2_confirmed_notification(  # noqa: PLR0913
    *,
    incident_id: int,
    well_id: int,
    opened_at: datetime,
    confirmed_at: datetime,
    evidence: Mapping[str, Any] | None,
    well_name: str | None = None,
    station: str | None = None,
) -> NotificationCreateDTO:
    """Проверка впервые подтвердила эпизод: первый ремонт — ПРС с обрывом.

    ``evidence`` — улики отметки (``repair`` — список с первым ремонтом).
    """
    repairs = (evidence or {}).get("repair")
    repair = repairs[0] if isinstance(repairs, list) and repairs else {}
    repair = repair if isinstance(repair, Mapping) else {}
    repair_id = repair.get("repair_id")
    type_name = str(repair.get("type_name") or "").strip()
    repair_text = ", ".join(
        part for part in (f"№ {repair_id}" if repair_id else "", type_name) if part
    )
    start = _parse_moment(repair.get("start"))
    facts = [
        _fact("Начало провала", fmt_dt(opened_at)),
        _fact("Ремонт", repair_text or None),
        _fact("Начало ремонта", fmt_dt(start) if start else None),
        _fact("Станция", station),
    ]
    return NotificationCreateDTO(
        event_code=R2_CONFIRMED,
        severity=SEVERITY_INFO,
        title_ru=well_title(well_name, "обрыв штанг подтверждён ремонтом"),
        body_ru=(
            f"Первый ремонт после сигнала от {fmt_dt(opened_at)} — ПРС с обрывом штанг."
        ),
        well_id=well_id,
        source_type=SOURCE_INCIDENT,
        source_id=incident_id,
        dedup_key=dedup_key(R2_CONFIRMED, SOURCE_INCIDENT, incident_id),
        payload={
            "incident_id": incident_id,
            "detector_code": "R2",
            "verdict": VERDICT_CONFIRMED,
            "repair_id": repair_id,
        },
        facts=[fact for fact in facts if fact is not None][:MAX_FACTS],
        event_at=confirmed_at,
    )


# --- R9: перекос нагрузки ---

_R9_CLOSE_TEXTS = {
    CLOSE_REASON_RECOVERED: "нагрузка в норме",
    CLOSE_REASON_STALE: "закрыт по давности",
    CLOSE_REASON_MANUAL: "закрыт вручную",
}
_R9_CLOSE_BODIES = {
    CLOSE_REASON_RECOVERED: (
        f"{r9_incident.RECOVER_SUSTAIN_DAYS} суток подряд нагрузка в норме — "
        "перекос нагрузки закрыт."
    ),
    CLOSE_REASON_STALE: (
        f"Больше {r9_incident.STALE_DAYS} суток нет пригодных данных станции — "
        "перекос нагрузки закрыт по давности."
    ),
    CLOSE_REASON_MANUAL: "Перекос нагрузки закрыт вручную.",
}


def _k(value: float) -> str:
    return _num(value, 2)


def _r9_facts(t: IncidentTransition) -> list[NotificationFactDTO | None]:
    incident = t.incident
    payload = incident.payload or {}
    facts = [_fact("Перекос с", _day(incident.opened_at))]
    if t.kind == TRANSITION_CLOSED:
        reason = incident.close_reason or CLOSE_REASON_RECOVERED
        text = _R9_CLOSE_TEXTS.get(reason, closed_label_ru("R9", reason))
        facts.append(_fact("Причина", _upper_first(text)))
        facts.append(_fact("Станция", t.station))
        return facts

    evidence = (
        payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    )
    last = _last(evidence)
    day = _parse_day(last.get("day"))
    k = _number(payload.get("k"))
    branches = payload.get("branches") or []
    baseline = (
        payload.get("baseline") if isinstance(payload.get("baseline"), Mapping) else {}
    )
    if k is not None:
        value = _k(k)
        k_alert = _number(payload.get("k_alert"))
        usual = _number(baseline.get("k_p90"))
        if "abs" in branches and k_alert is not None:
            value += f" при пороге {_k(k_alert)}"
        elif usual is not None:
            value += f", обычно до {_k(usual)}"
        facts.append(_fact(f"K за {_day(day)}" if day else "K за сутки", value))
    peak = _number(last.get("p95"))
    usual_peak = _number(baseline.get("p95_median"))
    if "loss" in branches and peak is not None and usual_peak:
        facts.append(_fact("Рабочий момент", f"{_pct(peak / usual_peak)} от обычного"))
    if incident.level == INCIDENT_LEVEL_ALARM:
        opened = incident.opened_at.date()
        alert_days = sum(
            1
            for item in evidence
            if isinstance(item, Mapping)
            and item.get("state") == "alert"
            and (_parse_day(item.get("day")) or opened) >= opened
        )
        facts.append(
            _fact("Суток с перекосом", str(alert_days) if alert_days else None),
        )
    facts.append(_fact("Станция", t.station))
    return facts


def r9_notification(t: IncidentTransition) -> NotificationCreateDTO:
    """Открытие, эскалация до аварии, закрытие (норма 4 суток или давность)."""
    incident = t.incident
    since = _day(incident.opened_at)
    facts = _r9_facts(t)
    event_at = discovered_at(t.at, t.detected_at)
    if t.kind == TRANSITION_CLOSED:
        reason = incident.close_reason or CLOSE_REASON_RECOVERED
        return _dto(
            t,
            event_code=R9_CLOSED,
            severity=SEVERITY_INFO,
            text=_R9_CLOSE_TEXTS.get(
                reason,
                lower_first(closed_label_ru("R9", reason)),
            ),
            body=_R9_CLOSE_BODIES.get(reason, "Перекос нагрузки закрыт."),
            facts=facts,
            event_at=event_at,
        )
    if incident.level == INCIDENT_LEVEL_ALARM:
        return _dto(
            t,
            event_code=R9_ALARM,
            severity=SEVERITY_ALARM,
            text="перекос нагрузки, авария",
            body=(
                f"Перекос нагрузки на ШГН не ушёл: с {since} это уже "
                f"{r9_incident.ALARM_ALERT_DAYS}-е сутки с перекосом. Скважину "
                "стоит проверить."
            ),
            facts=facts,
            event_at=event_at,
        )
    return _dto(
        t,
        event_code=R9_OPENED,
        severity=SEVERITY_WARNING,
        text="перекос нагрузки",
        body=(
            f"За {since} станция показала перекос нагрузки на ШГН: привод "
            "работает не так, как обычно для этой скважины. Если перекос "
            "повторится, сигнал станет аварией."
        ),
        facts=facts,
        event_at=event_at,
    )


# --- R10: дебит по замерам ЦИТС ---

_R10_CLOSE_TEXTS = {
    CLOSE_REASON_RECOVERED: "дебит восстановился",
    r10_incident.CLOSE_REASON_IDLE: "поставлен простой в ABAI",
    r10_incident.CLOSE_REASON_CHRONIC: "отклонение ушло в хронику",
    CLOSE_REASON_STALE: "закрыт по давности",
    CLOSE_REASON_MANUAL: "закрыт вручную",
}
_R10_CLOSE_BODIES = {
    CLOSE_REASON_RECOVERED: (
        "Последний замер ЦИТС снова в норме относительно техрежима — эпизод закрыт."
    ),
    r10_incident.CLOSE_REASON_IDLE: (
        "В ABAI на скважине поставлен простой: технологи в курсе, эпизод закрыт."
    ),
    r10_incident.CLOSE_REASON_CHRONIC: (
        f"Отклонение держится дольше {r10_config.FRESH_D} суток и ушло в хронику: "
        "оно остаётся в ведомости, эпизод закрыт."
    ),
    CLOSE_REASON_STALE: (
        "Свежих замеров ЦИТС нет — подтвердить нечем, эпизод закрыт по давности."
    ),
    CLOSE_REASON_MANUAL: "Эпизод закрыт вручную.",
}
_DROP_SHARE = 1 - r10_config.DEV_THR


def _r10_measure_facts(payload: Mapping[str, Any]) -> list[NotificationFactDTO | None]:
    q_last = _number(payload.get("q_last"))
    rezhim = _number(payload.get("rezhim"))
    dev = _number(payload.get("dev"))
    last_day = _parse_day(payload.get("last_day"))
    share = 1 + dev if dev is not None else None
    if share is None and q_last is not None and rezhim:
        share = q_last / rezhim
    measured = None
    if q_last is not None:
        measured = f"{_num(q_last, 1)} м³/сут"
        if last_day is not None:
            measured += f" ({_day(last_day)})"
    return [
        _fact("Последний замер Qж", measured),
        _fact("Техрежим Qж", f"{_num(rezhim, 1)} м³/сут" if rezhim else None),
        _fact("Доля режима", _pct(share) if share is not None else None),
    ]


def _r10_facts(t: IncidentTransition) -> list[NotificationFactDTO | None]:
    incident = t.incident
    payload = incident.payload or {}
    series_from = _parse_day(payload.get("series_from")) or incident.opened_at.date()
    if t.kind == TRANSITION_CLOSED:
        reason = incident.close_reason or CLOSE_REASON_RECOVERED
        text = _R10_CLOSE_TEXTS.get(reason, closed_label_ru("R10", reason))
        return [
            _fact("Серия с", _day(series_from)),
            _fact("Причина", _upper_first(text)),
            *_r10_measure_facts(payload)[:1],
        ]
    n_series = payload.get("n_series")
    series = _day(series_from)
    if isinstance(n_series, int) and n_series > 0:
        series += f", замеров: {n_series}"
    return [
        *_r10_measure_facts(payload),
        _fact("Серия с", series),
        _fact("СУ", payload.get("su") if isinstance(payload.get("su"), str) else None),
    ]


def r10_notification(t: IncidentTransition) -> NotificationCreateDTO:
    """Отклонение (warning), нули (alarm: открытие сразу или эскалация),
    закрытие по причине."""
    incident = t.incident
    facts = _r10_facts(t)
    event_at = discovered_at(t.at, t.detected_at)
    if t.kind == TRANSITION_CLOSED:
        reason = incident.close_reason or CLOSE_REASON_RECOVERED
        return _dto(
            t,
            event_code=R10_CLOSED,
            severity=SEVERITY_INFO,
            text=_R10_CLOSE_TEXTS.get(
                reason,
                lower_first(closed_label_ru("R10", reason)),
            ),
            body=_R10_CLOSE_BODIES.get(reason, "Эпизод закрыт."),
            facts=facts,
            event_at=event_at,
        )
    if incident.level == INCIDENT_LEVEL_ALARM:
        body = (
            "Дебит, который был ниже режима, упал до нуля: последние замеры "
            "ЦИТС нулевые."
            if t.kind == TRANSITION_ESCALATED
            else "Последние замеры ЦИТС подряд показывают нулевой дебит жидкости."
        )
        return _dto(
            t,
            event_code=R10_ZERO_LIQUID,
            severity=SEVERITY_ALARM,
            text="нулевой дебит",
            body=body,
            facts=facts,
            event_at=event_at,
        )
    return _dto(
        t,
        event_code=R10_LIQUID_DROP,
        severity=SEVERITY_WARNING,
        text="дебит ниже режима",
        body=(
            "Последние замеры ЦИТС подряд показывают дебит жидкости не выше "
            f"{_pct(_DROP_SHARE)} техрежима."
        ),
        facts=facts,
        event_at=event_at,
    )


_BUILDERS = {
    "R2": r2_notification,
    "R9": r9_notification,
    "R10": r10_notification,
}


def build_notification(t: IncidentTransition) -> NotificationCreateDTO | None:
    """Уведомление по переходу; правило без уведомлений — ``None``."""
    builder = _BUILDERS.get(t.incident.detector_code)
    return builder(t) if builder is not None else None


# --- Запись ---


async def _labels(
    session: AsyncSession,
    well_ids: Iterable[int],
    station_ids: Iterable[int],
) -> tuple[dict[int, str], dict[int, str]]:
    """Имена скважин и коды станций СДМО по id."""
    wells = set(well_ids)
    stations = set(station_ids)
    names: dict[int, str] = {}
    codes: dict[int, str] = {}
    if wells:
        rows = await session.execute(
            select(Well.id, Well.name).where(Well.id.in_(wells)),
        )
        names = {well_id: name for well_id, name in rows.all() if name}
    if stations:
        rows = await session.execute(
            select(
                SdmoStation.id,
                func.coalesce(SdmoStation.code, SdmoStation.name),
            ).where(SdmoStation.id.in_(stations)),
        )
        codes = {station_id: code for station_id, code in rows.all() if code}
    return names, codes


async def _with_labels(
    session: AsyncSession,
    transitions: Sequence[IncidentTransition],
) -> list[IncidentTransition]:
    names, codes = await _labels(
        session,
        (t.incident.well_id for t in transitions if t.well_name is None),
        (
            t.incident.entity_id
            for t in transitions
            if t.station is None and t.incident.entity_id is not None
        ),
    )
    return [
        replace(
            t,
            well_name=t.well_name or names.get(t.incident.well_id),
            station=t.station
            or (codes.get(t.incident.entity_id) if t.incident.entity_id else None),
        )
        for t in transitions
    ]


async def emit_transitions(
    session: AsyncSession,
    transitions: Sequence[IncidentTransition],
) -> list[int]:
    """Записать уведомления по переходам — в транзакции раннера, прямо перед
    его ``commit``.

    Сбой (подписи, построение, запись) детектор не роняет: всё в savepoint,
    ошибка — в лог, результат — пустой список.
    """
    if not transitions or not notification_emitter.enabled:
        return []
    try:
        async with session.begin_nested():
            resolved = await _with_labels(session, transitions)
            items = [
                item
                for item in (build_notification(t) for t in resolved)
                if item is not None
            ]
            return await emit_all(session, items)
    except Exception:
        logger.exception(
            "Detector notifications not written: incidents %s",
            [t.incident.id for t in transitions],
        )
        return []


async def emit_r2_confirmed(  # noqa: PLR0913
    session: AsyncSession,
    *,
    incident_id: int,
    well_id: int,
    station_id: int | None,
    opened_at: datetime,
    confirmed_at: datetime,
    evidence: Mapping[str, Any] | None,
) -> list[int]:
    """Записать ``r2.confirmed`` — прямо перед ``commit`` проверки; сбой не
    роняет проверку (savepoint, лог, пустой список)."""
    if not notification_emitter.enabled:
        return []
    try:
        async with session.begin_nested():
            names, codes = await _labels(
                session,
                [well_id],
                [station_id] if station_id is not None else [],
            )
            item = r2_confirmed_notification(
                incident_id=incident_id,
                well_id=well_id,
                opened_at=opened_at,
                confirmed_at=confirmed_at,
                evidence=evidence,
                well_name=names.get(well_id),
                station=codes.get(station_id) if station_id is not None else None,
            )
            return await emit_all(session, [item])
    except Exception:
        logger.exception("R2 confirmation notification not written: %s", incident_id)
        return []


async def publish_safely(ids: Sequence[int]) -> None:
    """Сигнал хабам после коммита; сбой не роняет таску (подберёт опрос хаба)."""
    if not ids:
        return
    try:
        await publish_notifications(ids)
    except Exception:
        logger.exception("Detector notifications signal failed: %s", list(ids))
