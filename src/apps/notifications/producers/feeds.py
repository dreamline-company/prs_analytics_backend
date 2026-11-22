"""Источники данных: сбои загрузок по НГДУ и защита ABAI (``feed.*``).

- ``feed.sdmo_outage`` / ``feed.sdmo_restored`` — сбой СДМО по НГДУ: переход
  ``wells_state_run.sdmo_outage_since`` из пустого в заполненное и обратно
  (``WellStateRefresher``). Цепочка — ``feed_source_id(НГДУ, начало сбоя)``.
- ``feed.cits_stale`` / ``feed.cits_restored`` — у НГДУ нет новых замеров ЦИТС
  ``CITS_STALE_AFTER`` (загрузчик WinCC, каждый час). Состояния сбоя в таблицах
  нет: открытый сбой — последний ``feed.cits_stale`` НГДУ в журнале без
  своего ``feed.cits_restored``. Цепочка — ``feed_source_id(НГДУ, последний
  замер)``.
- ``feed.abai_guard`` — ABAI вернул пустой ответ, защита не дала стереть
  статусы или пометить ремонты. Не больше одного в час на защиту.

Модуль только строит DTO; пишут их таски перед своим ``commit``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from apps.notifications.constants import SEVERITY_ALARM, SEVERITY_INFO, SEVERITY_WARNING
from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationFactDTO,
)
from apps.notifications.producers.common import (
    SOURCE_FEED_ABAI,
    SOURCE_FEED_CITS,
    SOURCE_FEED_SDMO,
    dedup_key,
    feed_source_id,
    fmt_dt,
    ngdu_name_ru,
)
from apps.notifications.producers.wells import fmt_duration, parse_moment

EVENT_SDMO_OUTAGE = "feed.sdmo_outage"
EVENT_SDMO_RESTORED = "feed.sdmo_restored"
EVENT_CITS_STALE = "feed.cits_stale"
EVENT_CITS_RESTORED = "feed.cits_restored"
EVENT_ABAI_GUARD = "feed.abai_guard"

# Нет новых замеров ЦИТС по НГДУ столько — сбой (предложено 23.11.2026: на
# проде за 14 дней самая длинная пауза — 1,5 ч, медианная — минуты).
CITS_STALE_AFTER = timedelta(hours=3)
# Последний замер для проверки сбоя берётся не позже «сейчас» + столько:
# допуск на спешащие часы ЦИТС ЖМГ. Допуск загрузки (сутки, ``future_limit``)
# здесь не годится — одна опечатка «из будущего» прятала бы сбой почти сутки.
CITS_CLOCK_TOLERANCE = timedelta(hours=1)

# Последняя цифра source_id защиты ABAI: какая защита сработала.
ABAI_GUARD_STATUSES = 1
ABAI_GUARD_REPAIRS = 2

_ABAI_GUARD_TITLES = {
    ABAI_GUARD_STATUSES: "ABAI: пустой ответ, статусы не удалены",
    ABAI_GUARD_REPAIRS: "ABAI: пропали незавершённые ремонты, ничего не помечено",
}
_ABAI_GUARD_OBJECTS = {
    ABAI_GUARD_STATUSES: "открытых интервалов статусов",
    ABAI_GUARD_REPAIRS: "незавершённых ремонтов",
}


# --- СДМО ---


def _stations_fact(
    reporting: int | None,
    base: int | None,
) -> NotificationFactDTO | None:
    if reporting is None or base is None:
        return None
    return NotificationFactDTO(
        label_ru="Станций на связи за 20 мин",
        value_ru=f"{reporting} из {base}",
    )


def sdmo_outage_notification(
    *,
    abai_ngdu_id: int,
    outage_since: datetime,
    reporting: int | None = None,
    base: int | None = None,
) -> NotificationCreateDTO:
    source_id = feed_source_id(abai_ngdu_id, outage_since)
    facts = [
        NotificationFactDTO(label_ru="Нет данных с", value_ru=fmt_dt(outage_since)),
    ]
    if (stations := _stations_fact(reporting, base)) is not None:
        facts.append(stations)
    return NotificationCreateDTO(
        event_code=EVENT_SDMO_OUTAGE,
        severity=SEVERITY_ALARM,
        title_ru=(
            f"СДМО {ngdu_name_ru(abai_ngdu_id)}: нет данных с {outage_since:%H:%M}"
        ),
        body_ru=(
            "За 20 минут отчиталось меньше половины станций НГДУ, "
            "отчитывавшихся за 7 суток."
        ),
        abai_ngdu_id=abai_ngdu_id,
        source_type=SOURCE_FEED_SDMO,
        source_id=source_id,
        dedup_key=dedup_key(EVENT_SDMO_OUTAGE, SOURCE_FEED_SDMO, source_id),
        payload={
            "outage_since": outage_since.isoformat(),
            "stations_reporting": reporting,
            "stations_base": base,
        },
        facts=facts,
        event_at=outage_since,
    )


def sdmo_restored_notification(
    *,
    abai_ngdu_id: int,
    outage_since: datetime,
    now: datetime,
    reporting: int | None = None,
    base: int | None = None,
) -> NotificationCreateDTO:
    source_id = feed_source_id(abai_ngdu_id, outage_since)
    facts = [
        NotificationFactDTO(label_ru="Не было данных с", value_ru=fmt_dt(outage_since)),
        NotificationFactDTO(
            label_ru="Длительность",
            value_ru=fmt_duration(now - outage_since),
        ),
    ]
    if (stations := _stations_fact(reporting, base)) is not None:
        facts.append(stations)
    return NotificationCreateDTO(
        event_code=EVENT_SDMO_RESTORED,
        severity=SEVERITY_INFO,
        title_ru=f"СДМО {ngdu_name_ru(abai_ngdu_id)}: данные снова идут",
        abai_ngdu_id=abai_ngdu_id,
        source_type=SOURCE_FEED_SDMO,
        source_id=source_id,
        dedup_key=dedup_key(EVENT_SDMO_RESTORED, SOURCE_FEED_SDMO, source_id),
        payload={
            "outage_since": outage_since.isoformat(),
            "stations_reporting": reporting,
            "stations_base": base,
        },
        facts=facts,
        event_at=now,
    )


def sdmo_feed_notification(  # noqa: PLR0913
    *,
    abai_ngdu_id: int,
    previous_since: datetime | None,
    current_since: datetime | None,
    now: datetime,
    reporting: int | None = None,
    base: int | None = None,
) -> NotificationCreateDTO | None:
    """Смена ``sdmo_outage_since`` за прогон: прошлое значение → новое.

    Нет сбоя → сбой — ``feed.sdmo_outage``, сбой → нет сбоя —
    ``feed.sdmo_restored``; без смены — ничего.
    """
    if previous_since is None and current_since is not None:
        return sdmo_outage_notification(
            abai_ngdu_id=abai_ngdu_id,
            outage_since=current_since,
            reporting=reporting,
            base=base,
        )
    if previous_since is not None and current_since is None:
        return sdmo_restored_notification(
            abai_ngdu_id=abai_ngdu_id,
            outage_since=previous_since,
            now=now,
            reporting=reporting,
            base=base,
        )
    return None


# --- ЦИТС ---


@dataclass(frozen=True, slots=True)
class CitsLoadResult:
    """Итог загрузки НГДУ в этом прогоне — для фактов уведомления."""

    read: int = 0
    added: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CitsStaleRef:
    """Последний ``feed.cits_stale`` НГДУ в журнале."""

    source_id: int
    last_measure_at: datetime | None
    restored: bool  # у него уже есть ``feed.cits_restored``


def cits_stale_ref(
    *,
    source_id: int,
    payload: Mapping[str, Any],
    restored: bool,
) -> CitsStaleRef:
    """Строка журнала ``feed.cits_stale`` → ссылка для решения."""
    return CitsStaleRef(
        source_id=source_id,
        last_measure_at=parse_moment(payload.get("last_measure_at")),
        restored=restored,
    )


def _load_text(load: CitsLoadResult | None) -> str | None:
    if load is None:
        return None
    if load.error is not None:
        return "ошибка загрузки"
    return f"новых замеров {load.added} (прочитано {load.read})"


def cits_stale_notification(
    *,
    abai_ngdu_id: int,
    last_measure_at: datetime,
    now: datetime,
    load: CitsLoadResult | None = None,
) -> NotificationCreateDTO:
    source_id = feed_source_id(abai_ngdu_id, last_measure_at)
    silent = fmt_duration(now - last_measure_at)
    facts = [
        NotificationFactDTO(
            label_ru="Последний замер",
            value_ru=fmt_dt(last_measure_at),
        ),
        NotificationFactDTO(label_ru="Нет замеров", value_ru=silent),
    ]
    if (load_text := _load_text(load)) is not None:
        facts.append(NotificationFactDTO(label_ru="Загрузка", value_ru=load_text))
    return NotificationCreateDTO(
        event_code=EVENT_CITS_STALE,
        severity=SEVERITY_ALARM,
        title_ru=(
            f"ЦИТС {ngdu_name_ru(abai_ngdu_id)}: нет замеров с "
            f"{fmt_dt(last_measure_at)}"
        ),
        body_ru=f"Новых замеров ЦИТС по НГДУ нет {silent}.",
        abai_ngdu_id=abai_ngdu_id,
        source_type=SOURCE_FEED_CITS,
        source_id=source_id,
        dedup_key=dedup_key(EVENT_CITS_STALE, SOURCE_FEED_CITS, source_id),
        payload={
            "last_measure_at": last_measure_at.isoformat(),
            "load_read": load.read if load is not None else None,
            "load_added": load.added if load is not None else None,
            "load_error": load.error if load is not None else None,
        },
        facts=facts,
        event_at=now,
    )


def cits_restored_notification(
    *,
    abai_ngdu_id: int,
    stale: CitsStaleRef,
    last_measure_at: datetime,
    now: datetime,
) -> NotificationCreateDTO:
    facts = []
    if stale.last_measure_at is not None:
        facts.append(
            NotificationFactDTO(
                label_ru="Не было замеров с",
                value_ru=fmt_dt(stale.last_measure_at),
            ),
        )
    facts.append(
        NotificationFactDTO(
            label_ru="Последний замер",
            value_ru=fmt_dt(last_measure_at),
        ),
    )
    return NotificationCreateDTO(
        event_code=EVENT_CITS_RESTORED,
        severity=SEVERITY_INFO,
        title_ru=f"ЦИТС {ngdu_name_ru(abai_ngdu_id)}: замеры снова идут",
        abai_ngdu_id=abai_ngdu_id,
        source_type=SOURCE_FEED_CITS,
        source_id=stale.source_id,
        dedup_key=dedup_key(EVENT_CITS_RESTORED, SOURCE_FEED_CITS, stale.source_id),
        payload={"last_measure_at": last_measure_at.isoformat()},
        facts=facts,
        event_at=now,
    )


def cits_feed_notification(  # noqa: PLR0913
    *,
    abai_ngdu_id: int,
    last_measure_at: datetime,
    now: datetime,
    previous: CitsStaleRef | None,
    load: CitsLoadResult | None = None,
    stale_after: timedelta = CITS_STALE_AFTER,
) -> NotificationCreateDTO | None:
    """Решение после загрузки НГДУ по последнему замеру у нас.

    - Замеров нет ``stale_after`` и открытого сбоя нет — ``feed.cits_stale``.
    - Открытый сбой (последний stale без restored) продолжается — ничего: и
      когда последний замер тот же (тот же источник), и когда пришло немного
      замеров, но порог не догнан — один сбой, одна цепочка.
    - Замер свежий (в том числе «из будущего» — часы ЦИТС ЖМГ спешат) и новее
      ``last_measure_at`` открытого сбоя — ``feed.cits_restored`` в его цепочку.
    """
    open_stale = previous if previous is not None and not previous.restored else None
    if now - last_measure_at >= stale_after:
        if open_stale is not None:
            return None
        return cits_stale_notification(
            abai_ngdu_id=abai_ngdu_id,
            last_measure_at=last_measure_at,
            now=now,
            load=load,
        )
    if open_stale is None:
        return None
    if (
        open_stale.last_measure_at is not None
        and last_measure_at <= open_stale.last_measure_at
    ):
        return None
    return cits_restored_notification(
        abai_ngdu_id=abai_ngdu_id,
        stale=open_stale,
        last_measure_at=last_measure_at,
        now=now,
    )


# --- ABAI ---


def abai_guard_source_id(guard: int, now: datetime) -> int:
    """Час срабатывания и номер защиты: ``2026112310`` + ``1``."""
    return int(f"{now:%Y%m%d%H}{guard}")


def abai_guard_notification(
    *,
    guard: int,
    gone: int,
    checked: int,
    now: datetime,
) -> NotificationCreateDTO:
    """``guard`` — ``ABAI_GUARD_STATUSES`` или ``ABAI_GUARD_REPAIRS``."""
    source_id = abai_guard_source_id(guard, now)
    objects = _ABAI_GUARD_OBJECTS[guard]
    return NotificationCreateDTO(
        event_code=EVENT_ABAI_GUARD,
        severity=SEVERITY_WARNING,
        title_ru=_ABAI_GUARD_TITLES[guard],
        body_ru=(
            f"В ответе ABAI нет {gone} из {checked} {objects}: похоже на сбой "
            "чтения источника, а не на удаление. Ничего не изменено."
        ),
        source_type=SOURCE_FEED_ABAI,
        source_id=source_id,
        dedup_key=dedup_key(EVENT_ABAI_GUARD, SOURCE_FEED_ABAI, source_id),
        payload={"guard": guard, "gone": gone, "checked": checked},
        facts=[
            NotificationFactDTO(label_ru="Пропало", value_ru=f"{gone} из {checked}"),
            NotificationFactDTO(label_ru="Что проверялось", value_ru=objects),
        ],
        event_at=now,
    )
