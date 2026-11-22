"""Уведомления по эпизодам R2 / R9 / R10: тексты, уровни, ключи, event_at и
решения «шлём / не шлём» — на подделках, без БД."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from apps.detectors.cits_events.rule import EVENT_DROP, EVENT_ZERO, Event, WellVerdict
from apps.detectors.cits_events.services.data_source import WellRecord
from apps.detectors.cits_events.services.episode import (
    ACTION_CLOSE,
    ACTION_UPSERT,
    EpisodeDecision,
)
from apps.detectors.cits_events.tasks.run_incidents import (
    run_incidents as r10_run,
)
from apps.detectors.dto.internal.repositories.incident import (
    IncidentStateDTO,
    OpenIncidentDTO,
)
from apps.detectors.load_imbalance.services import episode as r9_episode
from apps.detectors.load_imbalance.tasks.run_incidents import (
    run_incidents as r9_run,
)
from apps.detectors.models.verification import (
    VERDICT_CONFIRMED,
    VERDICT_NOT_CONFIRMED,
    VERDICT_PENDING,
)
from apps.detectors.rod_breaks.services import episode as r2_episode
from apps.detectors.rod_breaks.tasks.run_incidents import (
    run_incidents as r2_run,
)
from apps.detectors.rod_breaks.tasks.verify import verify as r2_verify
from apps.detectors.rod_breaks.verification.data_source import IncidentRef
from apps.detectors.rod_breaks.verification.rule import VerificationResult
from apps.notifications.constants import MAX_EVENT_AGE
from apps.notifications.producers import detectors as producer
from apps.notifications.producers.detectors import (
    TRANSITION_CLOSED,
    TRANSITION_ESCALATED,
    TRANSITION_OPENED,
    IncidentTransition,
    build_notification,
    discovered_at,
    is_new_confirmation,
    live_run_notifies,
    r2_confirmed_notification,
    r10_run_notifies,
    verify_run_notifies,
)
from apps.notifications.services.emitter import (
    NotificationEmitter,
    notification_emitter,
)
from shared.constants.ngdu import AbaiNGDUIDsEnum

# Местное наивное время: утро после суток 22.11.
NOW = datetime(2026, 11, 23, 4, 10)  # noqa: DTZ001
T0 = datetime(2026, 11, 23, 1, 0)  # noqa: DTZ001
YESTERDAY = date(2026, 11, 22)
MIDNIGHT = datetime(2026, 11, 23)  # noqa: DTZ001 — конец суток 22.11
DAY_BEFORE_MIDNIGHT = datetime(2026, 11, 22)  # noqa: DTZ001 — конец суток 21.11


def _state(  # noqa: PLR0913
    detector_code: str,
    *,
    incident_id: int = 42,
    level: str = "warning",
    opened_at: datetime = T0,
    status: str = "active",
    close_reason: str | None = None,
    normalized_at: datetime | None = None,
    escalated_at: datetime | None = None,
    payload: dict | None = None,
    entity_id: int | None = 10,
) -> IncidentStateDTO:
    return IncidentStateDTO(
        id=incident_id,
        detector_code=detector_code,
        well_id=5,
        entity_id=entity_id,
        level=level,
        status=status,
        opened_at=opened_at,
        detected_at=opened_at,
        escalated_at=escalated_at,
        normalized_at=normalized_at,
        close_reason=close_reason,
        payload=payload,
    )


def _t(  # noqa: PLR0913
    kind: str,
    incident: IncidentStateDTO,
    *,
    at: datetime,
    detected_at: datetime = NOW,
    well_name: str | None = "VMB_2051",
    station: str | None = "ST_2051",
) -> IncidentTransition:
    return IncidentTransition(
        kind=kind,
        incident=incident,
        at=at,
        detected_at=detected_at,
        well_name=well_name,
        station=station,
    )


def _facts(dto: Any) -> dict[str, str]:  # noqa: ANN401
    return {fact.label_ru: fact.value_ru for fact in dto.facts}


R2_PAYLOAD = {
    "base_t0": 410.0,
    "base_moment": 380.0,
    "evidence": [
        {"bucket": "2026-11-23T01:45:00", "mom_min": 120.0, "spd": 52.0},
    ],
}


# --- R2 ---


def test_r2_load_drop() -> None:
    dto = build_notification(
        _t(TRANSITION_OPENED, _state("R2", payload=R2_PAYLOAD), at=T0),
    )

    assert dto is not None
    assert dto.event_code == "r2.load_drop"
    assert dto.severity == "warning"
    assert dto.title_ru == "VMB_2051: падение нагрузки"
    assert dto.dedup_key == "r2.load_drop:detectors_incident:42"
    assert (dto.source_type, dto.source_id, dto.well_id) == (
        "detectors_incident",
        42,
        5,
    )
    # Время данных: начало провала.
    assert dto.event_at == T0
    assert _facts(dto) == {
        "Начало провала": "23.11 01:00",
        "Момент сейчас": "120 при базе 410 (29 %)",
        "Скорость": "52 об/мин",
        "Станция": "ST_2051",
    }
    assert dto.payload == {
        "incident_id": 42,
        "detector_code": "R2",
        "level": "warning",
        "close_reason": None,
    }
    assert "60 %" in (dto.body_ru or "")


def test_r2_escalation_is_alarm_dated_by_check_bucket() -> None:
    escalated = T0 + timedelta(hours=4)
    payload = {
        **R2_PAYLOAD,
        "stage_check": {"level": 140.0, "base": 410.0, "share_of_base": 0.341},
    }
    dto = build_notification(
        _t(
            TRANSITION_ESCALATED,
            _state("R2", level="alarm", escalated_at=escalated, payload=payload),
            at=escalated,
        ),
    )

    assert dto is not None
    assert dto.event_code == "r2.possible_rod_break"
    assert dto.severity == "alarm"
    assert dto.title_ru == "VMB_2051: возможный обрыв штанг"
    assert dto.dedup_key == "r2.possible_rod_break:detectors_incident:42"
    assert dto.event_at == escalated
    assert _facts(dto)["Момент за последний час"] == "140 при базе 410 (34 %)"


def test_r2_legacy_open_alarm_is_possible_rod_break() -> None:
    dto = build_notification(
        _t(TRANSITION_OPENED, _state("R2", level="alarm", payload=R2_PAYLOAD), at=T0),
    )

    assert dto is not None
    assert dto.event_code == "r2.possible_rod_break"
    assert _facts(dto)["Момент сейчас"] == "120 при базе 410 (29 %)"


@pytest.mark.parametrize(
    ("reason", "title", "late"),
    [
        ("recovered", "VMB_2051: момент восстановился", False),
        ("repair", "VMB_2051: начался ремонт", True),
        ("stale", "VMB_2051: закрыт по давности", True),
    ],
)
def test_r2_closed_by_reason(reason: str, title: str, late: bool) -> None:  # noqa: FBT001
    closed_at = T0 + timedelta(hours=6)
    dto = build_notification(
        _t(
            TRANSITION_CLOSED,
            _state(
                "R2",
                level="alarm",
                status="normalized",
                close_reason=reason,
                normalized_at=closed_at,
                payload=R2_PAYLOAD,
            ),
            at=closed_at,
        ),
    )

    assert dto is not None
    assert dto.event_code == "r2.closed"
    assert dto.severity == "info"
    assert dto.title_ru == title
    # Ремонт и давность — момент прогона, восстановление — время данных.
    assert dto.event_at == (NOW if late else closed_at)
    assert dto.payload["close_reason"] == reason
    assert "«Возможный обрыв штанг»" in (dto.body_ru or "")
    assert _facts(dto)["Закрыт"] == "23.11 07:00"


@pytest.mark.parametrize("reason", ["repair", "stale"])
def test_r2_late_close_survives_emitter_age_guard(reason: str) -> None:
    # Ремонт внесли задним числом / станция молчала: закрытие по данным —
    # трое суток назад, но уведомление пишется, время данных — в фактах.
    closed_at = NOW - timedelta(days=3)
    dto = build_notification(
        _t(
            TRANSITION_CLOSED,
            _state(
                "R2",
                level="alarm",
                status="normalized",
                close_reason=reason,
                normalized_at=closed_at,
            ),
            at=closed_at,
        ),
    )

    assert dto is not None
    assert dto.event_at == NOW
    assert _facts(dto)["Закрыт"] == "20.11 04:10"
    recovered = build_notification(
        _t(
            TRANSITION_CLOSED,
            _state("R2", status="normalized", close_reason="recovered"),
            at=closed_at,
        ),
    )
    assert recovered is not None
    assert recovered.event_at == closed_at


def test_r2_confirmed() -> None:
    dto = r2_confirmed_notification(
        incident_id=42,
        well_id=5,
        opened_at=T0,
        confirmed_at=NOW,
        evidence={
            "repair": [
                {
                    "repair_id": 77,
                    "start": "2026-11-23T03:00:00",
                    "type_name": "ПРС: обрыв штанг",
                },
            ],
        },
        well_name="VMB_2051",
        station="ST_2051",
    )

    assert dto.event_code == "r2.confirmed"
    assert dto.severity == "info"
    assert dto.title_ru == "VMB_2051: обрыв штанг подтверждён ремонтом"
    assert dto.dedup_key == "r2.confirmed:detectors_incident:42"
    assert dto.event_at == NOW
    assert _facts(dto) == {
        "Начало провала": "23.11 01:00",
        "Ремонт": "№ 77, ПРС: обрыв штанг",
        "Начало ремонта": "23.11 03:00",
        "Станция": "ST_2051",
    }


def test_r2_confirmed_without_repair_details_and_name() -> None:
    dto = r2_confirmed_notification(
        incident_id=42,
        well_id=5,
        opened_at=T0,
        confirmed_at=NOW,
        evidence={},
    )

    assert dto.title_ru == "Обрыв штанг подтверждён ремонтом"
    assert list(_facts(dto)) == ["Начало провала"]


# --- R9 ---

R9_PAYLOAD = {
    "k": 0.31,
    "k_alert": 0.2,
    "branches": ["abs"],
    "baseline": {"n_days": 30, "k_p90": 0.06, "p95_median": 900.0},
    "evidence": [
        {"day": "2026-11-21", "state": "alert", "k": 0.25, "p95": 800.0},
        {"day": "2026-11-22", "state": "alert", "k": 0.31, "p95": 780.0},
    ],
}


def test_r9_opened_uses_detection_time() -> None:
    # Открытие датировано началом вчерашних суток — к 04:10 это 28 ч:
    # event_at — момент прогона, начало — в фактах.
    opened_at = datetime.combine(YESTERDAY, datetime.min.time())
    dto = build_notification(
        _t(
            TRANSITION_OPENED,
            _state("R9", opened_at=opened_at, payload=R9_PAYLOAD),
            at=datetime(2026, 11, 23),  # noqa: DTZ001 — конец суток 22.11
        ),
    )

    assert dto is not None
    assert dto.event_code == "r9.opened"
    assert dto.severity == "warning"
    assert dto.title_ru == "VMB_2051: перекос нагрузки"
    assert dto.dedup_key == "r9.opened:detectors_incident:42"
    assert dto.event_at == NOW
    assert NOW - opened_at > MAX_EVENT_AGE
    assert _facts(dto) == {
        "Перекос с": "22.11",
        "K за 22.11": "0,31 при пороге 0,20",
        "Станция": "ST_2051",
    }


def test_r9_alarm_and_closed() -> None:
    opened_at = datetime(2026, 11, 21)  # noqa: DTZ001
    alarm = build_notification(
        _t(
            TRANSITION_ESCALATED,
            _state("R9", level="alarm", opened_at=opened_at, payload=R9_PAYLOAD),
            at=datetime(2026, 11, 23),  # noqa: DTZ001
        ),
    )
    closed = build_notification(
        _t(
            TRANSITION_CLOSED,
            _state(
                "R9",
                level="alarm",
                opened_at=opened_at,
                status="normalized",
                close_reason="recovered",
            ),
            at=datetime(2026, 11, 23),  # noqa: DTZ001
        ),
    )
    stale = build_notification(
        _t(
            TRANSITION_CLOSED,
            _state("R9", status="normalized", close_reason="stale"),
            at=NOW,
        ),
    )

    assert alarm is not None
    assert alarm.event_code == "r9.alarm"
    assert alarm.severity == "alarm"
    assert alarm.title_ru == "VMB_2051: перекос нагрузки, авария"
    assert _facts(alarm)["Суток с перекосом"] == "2"
    assert closed is not None
    assert closed.event_code == "r9.closed"
    assert closed.severity == "info"
    assert closed.title_ru == "VMB_2051: нагрузка в норме"
    assert closed.event_at == NOW
    assert stale is not None
    assert stale.title_ru == "VMB_2051: закрыт по давности"


def test_r9_catch_up_of_old_day_keeps_old_time() -> None:
    # Догон: событие стало известно трое суток назад — event_at остаётся
    # старым, и эмиттер его не пишет.
    old = datetime(2026, 11, 20)  # noqa: DTZ001
    dto = build_notification(
        _t(TRANSITION_OPENED, _state("R9", payload=R9_PAYLOAD), at=old),
    )

    assert dto is not None
    assert dto.event_at == old
    emitter = NotificationEmitter(enabled=True, clock=lambda: NOW)
    assert asyncio.run(emitter.emit(_NoDbSession(), dto)) is None  # type: ignore[arg-type]


def test_discovered_at() -> None:
    assert discovered_at(NOW - timedelta(hours=23), NOW) == NOW
    assert discovered_at(NOW - MAX_EVENT_AGE, NOW) == NOW
    old = NOW - MAX_EVENT_AGE - timedelta(minutes=1)
    assert discovered_at(old, NOW) == old


# --- R10 ---

R10_PAYLOAD = {
    "abai_ngdu_id": 4,
    "event": EVENT_DROP,
    "last_day": "2026-11-22",
    "q_last": 12.3,
    "rezhim": 27.0,
    "dev": -0.544,
    "n_series": 3,
    "series_from": "2026-11-19",
    "su": "СУ работает",
}


def test_r10_liquid_drop() -> None:
    dto = build_notification(
        _t(
            TRANSITION_OPENED,
            _state(
                "R10",
                opened_at=datetime(2026, 11, 19),  # noqa: DTZ001
                payload=R10_PAYLOAD,
                entity_id=None,
            ),
            at=datetime(2026, 11, 23),  # noqa: DTZ001
            well_name="ZHM_0101",
            station=None,
        ),
    )

    assert dto is not None
    assert dto.event_code == "r10.liquid_drop"
    assert dto.severity == "warning"
    assert dto.title_ru == "ZHM_0101: дебит ниже режима"
    assert dto.dedup_key == "r10.liquid_drop:detectors_incident:42"
    assert dto.event_at == NOW
    assert "70 %" in (dto.body_ru or "")
    assert _facts(dto) == {
        "Последний замер Qж": "12,3 м³/сут (22.11)",
        "Техрежим Qж": "27,0 м³/сут",
        "Доля режима": "46 %",
        "Серия с": "19.11, замеров: 3",
        "СУ": "СУ работает",
    }


@pytest.mark.parametrize("kind", [TRANSITION_OPENED, TRANSITION_ESCALATED])
def test_r10_zero_liquid(kind: str) -> None:
    payload = {**R10_PAYLOAD, "event": EVENT_ZERO, "q_last": 0.0, "dev": None}
    dto = build_notification(
        _t(
            kind,
            _state("R10", level="alarm", payload=payload, entity_id=None),
            at=NOW,
            well_name="ZHM_0101",
            station=None,
        ),
    )

    assert dto is not None
    assert dto.event_code == "r10.zero_liquid"
    assert dto.severity == "alarm"
    assert dto.title_ru == "ZHM_0101: нулевой дебит"
    assert dto.event_at == NOW
    assert _facts(dto)["Доля режима"] == "0 %"
    escalated = kind == TRANSITION_ESCALATED
    assert ("упал до нуля" in (dto.body_ru or "")) is escalated


@pytest.mark.parametrize(
    ("reason", "title"),
    [
        ("recovered", "ZHM_0101: дебит восстановился"),
        ("idle", "ZHM_0101: поставлен простой в ABAI"),
        ("chronic", "ZHM_0101: отклонение ушло в хронику"),
        ("stale", "ZHM_0101: закрыт по давности"),
    ],
)
def test_r10_closed_by_reason(reason: str, title: str) -> None:
    dto = build_notification(
        _t(
            TRANSITION_CLOSED,
            _state(
                "R10",
                status="normalized",
                close_reason=reason,
                payload=R10_PAYLOAD,
                entity_id=None,
            ),
            at=datetime(2026, 11, 23),  # noqa: DTZ001
            well_name="ZHM_0101",
            station=None,
        ),
    )

    assert dto is not None
    assert dto.event_code == "r10.closed"
    assert dto.severity == "info"
    assert dto.title_ru == title
    assert dto.dedup_key == "r10.closed:detectors_incident:42"
    assert 2 <= len(dto.facts) <= 5


def test_facts_are_two_to_five_everywhere() -> None:
    for code, payload in (("R2", R2_PAYLOAD), ("R9", R9_PAYLOAD), ("R10", R10_PAYLOAD)):
        for kind in (TRANSITION_OPENED, TRANSITION_ESCALATED, TRANSITION_CLOSED):
            dto = build_notification(
                _t(kind, _state(code, payload=payload, close_reason="stale"), at=NOW),
            )
            assert dto is not None
            assert 2 <= len(dto.facts) <= 5, (code, kind)


def test_unknown_detector_has_no_notification() -> None:
    assert build_notification(_t(TRANSITION_OPENED, _state("R11"), at=NOW)) is None


# --- Шлём / не шлём ---


def test_live_run_notifies() -> None:
    assert live_run_notifies(notify=True, has_cursor=True)
    assert not live_run_notifies(notify=True, has_cursor=False)  # первый прогон
    assert not live_run_notifies(notify=False, has_cursor=True)


def test_r10_run_notifies() -> None:
    assert r10_run_notifies(notify=True, fix_date=None, has_cursor=True)
    assert not r10_run_notifies(notify=True, fix_date=YESTERDAY, has_cursor=True)
    assert not r10_run_notifies(notify=True, fix_date=None, has_cursor=False)
    assert not r10_run_notifies(notify=False, fix_date=None, has_cursor=True)


def test_verify_run_notifies() -> None:
    base = {
        "dry_run": False,
        "incident_ids": None,
        "backfill_days": None,
        "recheck_days": None,
    }
    assert verify_run_notifies(**base)
    assert not verify_run_notifies(**{**base, "dry_run": True})
    assert not verify_run_notifies(**{**base, "incident_ids": [1]})
    assert not verify_run_notifies(**{**base, "backfill_days": 60})
    assert not verify_run_notifies(**{**base, "recheck_days": 60})


FRESH = NOW - timedelta(hours=47)
OLD = NOW - timedelta(hours=49)


@pytest.mark.parametrize(
    ("before", "after", "written", "evidence_at", "rule_changed", "expected"),
    [
        (None, VERDICT_CONFIRMED, True, FRESH, False, True),
        (VERDICT_PENDING, VERDICT_CONFIRMED, True, FRESH, False, True),
        (VERDICT_CONFIRMED, VERDICT_CONFIRMED, True, FRESH, False, False),
        (VERDICT_PENDING, VERDICT_CONFIRMED, False, FRESH, False, False),
        (None, VERDICT_PENDING, True, FRESH, False, False),
        # Отметка старой версии правила, но улика свежая — настоящее
        # подтверждение, шлём.
        (VERDICT_PENDING, VERDICT_CONFIRMED, True, FRESH, True, True),
        # Улика старше 48 ч — пересборка истории, не шлём.
        (VERDICT_NOT_CONFIRMED, VERDICT_CONFIRMED, True, OLD, True, False),
        (None, VERDICT_CONFIRMED, True, OLD, False, False),
        # Дат улики нет — как раньше: решает версия правила.
        (None, VERDICT_CONFIRMED, True, None, False, True),
        (VERDICT_PENDING, VERDICT_CONFIRMED, True, None, True, False),
    ],
)
def test_is_new_confirmation(  # noqa: PLR0913
    before: str | None,
    after: str,
    written: bool,  # noqa: FBT001
    evidence_at: datetime | None,
    rule_changed: bool,  # noqa: FBT001
    expected: bool,  # noqa: FBT001
) -> None:
    assert (
        is_new_confirmation(
            verdict_before=before,
            verdict_after=after,
            written=written,
            now=NOW,
            evidence_at=evidence_at,
            rule_changed=rule_changed,
        )
        is expected
    )


def test_confirmation_freshness_counts_repair_dates() -> None:
    def new(evidence: dict) -> bool:
        return is_new_confirmation(
            verdict_before=VERDICT_PENDING,
            verdict_after=VERDICT_CONFIRMED,
            written=True,
            now=NOW,
            evidence_at=NOW - timedelta(days=5),  # начало ремонта
            evidence=evidence,
        )

    start = (NOW - timedelta(days=5)).isoformat()
    day_ago = (NOW - timedelta(days=1)).isoformat()
    # Ремонт кончился сутки назад — работы дописали, улика свежая.
    assert new({"repair": [{"start": start, "end": day_ago}]})
    # Ремонт без конца ещё идёт.
    assert new({"repair": [{"start": start, "end": None}]})
    # Ремонт кончился 4 суток назад — пересборка.
    assert not new(
        {"repair": [{"start": start, "end": (NOW - timedelta(days=4)).isoformat()}]},
    )
    # Висящий ремонт без конца старше REPAIR_OPEN_MAX_DAYS — не «идёт».
    hanging = (NOW - timedelta(days=20)).isoformat()
    assert not new({"repair": [{"start": hanging, "end": None}]})


# --- Запись: подписи, savepoint, сбои ---


class _NoDbSession:
    async def execute(self, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        msg = "DB must not be touched"
        raise AssertionError(msg)


class _Rows:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _LabelSession:
    """Отвечает на запросы имён скважин и кодов станций."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.savepoints = 0
        self.queries: list[str] = []

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        self.savepoints += 1
        yield

    async def execute(self, stmt: Any) -> _Rows:  # noqa: ANN401
        if self.fail:
            msg = "boom"
            raise SQLAlchemyError(msg)
        sql = str(stmt)
        self.queries.append(sql)
        if "telemetry_sdmo_station" in sql:
            return _Rows([(10, "ST_2051")])
        return _Rows([(5, "VMB_2051")])


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list:
    items: list = []

    async def fake_emit_all(_session: Any, dtos: Any) -> list[int]:  # noqa: ANN401
        items.extend(dtos)
        return list(range(1, len(items) + 1))

    monkeypatch.setattr(producer, "emit_all", fake_emit_all)
    monkeypatch.setattr(notification_emitter, "_enabled", True)
    return items


def test_emit_transitions_resolves_labels(captured: list) -> None:
    session = _LabelSession()
    transition = _t(
        TRANSITION_OPENED,
        _state("R2", payload=R2_PAYLOAD),
        at=T0,
        well_name=None,
        station=None,
    )

    ids = asyncio.run(producer.emit_transitions(session, [transition]))  # type: ignore[arg-type]

    assert ids == [1]
    assert session.savepoints == 1
    assert captured[0].title_ru == "VMB_2051: падение нагрузки"
    assert _facts(captured[0])["Станция"] == "ST_2051"


def test_emit_transitions_failure_does_not_raise(captured: list) -> None:
    session = _LabelSession(fail=True)
    transition = _t(TRANSITION_OPENED, _state("R2"), at=T0, well_name=None)

    ids = asyncio.run(producer.emit_transitions(session, [transition]))  # type: ignore[arg-type]

    assert ids == []
    assert captured == []


def test_emit_transitions_disabled_skips_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notification_emitter, "_enabled", False)
    transition = _t(TRANSITION_OPENED, _state("R2"), at=T0, well_name=None)

    ids = asyncio.run(producer.emit_transitions(_NoDbSession(), [transition]))  # type: ignore[arg-type]

    assert ids == []


def test_emit_r2_confirmed_failure_does_not_raise(captured: list) -> None:
    ids = asyncio.run(
        producer.emit_r2_confirmed(
            _LabelSession(fail=True),  # type: ignore[arg-type]
            incident_id=42,
            well_id=5,
            station_id=10,
            opened_at=T0,
            confirmed_at=NOW,
            evidence={},
        ),
    )

    assert ids == []
    assert captured == []


def test_publish_safely_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer,
        "publish_notifications",
        AsyncMock(side_effect=RuntimeError("redis")),
    )
    asyncio.run(producer.publish_safely([1]))


# --- Хуки раннеров: порядок emit -> commit -> publish и «не шлём» ---


class _OrderSession:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


class _Cursors:
    def __init__(self, cursors: dict[int, Any]) -> None:
        self.cursors = cursors

    async def get_map(self, _code: str, _ids: Any = None) -> dict[int, Any]:  # noqa: ANN401
        return self.cursors


def _patch_emit(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,  # noqa: ANN401
    calls: list,
) -> None:
    async def fake_emit(_session: Any, transitions: Any) -> list[int]:  # noqa: ANN401
        calls.append(("emit", len(transitions)))
        return [101]

    async def fake_publish(ids: Any) -> None:  # noqa: ANN401
        calls.append(("publish", list(ids)))

    monkeypatch.setattr(module, "emit_transitions", fake_emit)
    monkeypatch.setattr(module, "publish_safely", fake_publish)


def _station_run(transition_kind: str = TRANSITION_OPENED) -> Any:  # noqa: ANN401
    async def run_station(
        _station: Any,  # noqa: ANN401
        *,
        cursor_ts: Any,  # noqa: ANN401, ARG001
        transitions: list,
    ) -> tuple[int, int, int]:
        transitions.append(_t(transition_kind, _state("R2"), at=T0))
        return (0, 0, 0)

    return run_station


@pytest.mark.parametrize(
    ("module", "runner_cls"),
    [
        (r2_run, r2_run.RodBreakIncidentRunner),
        (r9_run, r9_run.LoadImbalanceIncidentRunner),
    ],
)
@pytest.mark.parametrize("has_cursor", [True, False])
def test_station_runners_emit_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,  # noqa: ANN401
    runner_cls: Any,  # noqa: ANN401
    has_cursor: bool,  # noqa: FBT001
) -> None:
    calls: list = []
    _patch_emit(monkeypatch, module, calls)
    monkeypatch.setattr(
        module,
        "target_stations",
        AsyncMock(return_value=[SimpleNamespace(id=10, well_id=5)]),
    )
    runner = runner_cls.__new__(runner_cls)
    runner.session = _OrderSession(calls)
    runner.notify = True
    runner.cursor_repo = _Cursors(
        {10: SimpleNamespace(last_event_at=T0)} if has_cursor else {},
    )
    runner._run_station = _station_run()  # noqa: SLF001

    asyncio.run(runner.run())

    if has_cursor:
        assert calls == [("emit", 1), "commit", ("publish", [101])]
    else:
        # Первый прогон станции — разбор истории: не шлём.
        assert calls == ["commit", ("publish", [])]


def test_station_runner_failure_rolls_back_without_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    _patch_emit(monkeypatch, r2_run, calls)
    monkeypatch.setattr(
        r2_run,
        "target_stations",
        AsyncMock(return_value=[SimpleNamespace(id=10, well_id=5)]),
    )
    runner = r2_run.RodBreakIncidentRunner.__new__(r2_run.RodBreakIncidentRunner)
    runner.session = _OrderSession(calls)
    runner.notify = True
    runner.cursor_repo = _Cursors({10: SimpleNamespace(last_event_at=T0)})
    runner._run_station = AsyncMock(side_effect=RuntimeError("station"))  # noqa: SLF001

    asyncio.run(runner.run())

    assert calls == ["rollback"]


class _IncidentRepo:
    """upsert/normalize отдают эпизод, как RETURNING."""

    def __init__(self, detector_code: str) -> None:
        self.detector_code = detector_code
        self.level: str | None = None
        self.opened_at: datetime | None = None
        self.payload: dict | None = None

    async def get_active(self, **_: Any) -> None:  # noqa: ANN401
        return None

    async def get_by_opened(self, **_: Any) -> None:  # noqa: ANN401
        return None

    async def upsert_active(self, data: OpenIncidentDTO) -> IncidentStateDTO:
        if self.level != "alarm":
            self.level = data.level
        self.opened_at = self.opened_at or data.opened_at
        self.payload = data.payload
        return _state(
            self.detector_code,
            level=self.level,
            opened_at=self.opened_at,
            escalated_at=data.escalated_at,
            payload=data.payload,
        )

    async def normalize(
        self,
        *,
        normalized_at: datetime,
        close_reason: str = "recovered",
        **_: Any,  # noqa: ANN401
    ) -> IncidentStateDTO:
        return _state(
            self.detector_code,
            level=self.level or "warning",
            opened_at=self.opened_at or T0,
            status="normalized",
            close_reason=close_reason,
            normalized_at=normalized_at,
            payload=self.payload,
        )


class _NoopCursors:
    async def upsert(self, **_: Any) -> None:  # noqa: ANN401
        return None


def test_r2_station_records_transitions_with_data_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    escalated = T0 + timedelta(hours=4)
    closed = T0 + timedelta(hours=6)
    actions = [
        r2_episode.EpisodeAction(
            r2_episode.ACTION_OPEN_WARNING,
            T0,
            base=410.0,
            base_samples=100,
        ),
        r2_episode.EpisodeAction(r2_episode.ACTION_CONFIRM, T0 + timedelta(hours=2)),
        r2_episode.EpisodeAction(
            r2_episode.ACTION_ESCALATE,
            escalated,
            evidence={"level": 140.0, "base": 410.0},
        ),
        r2_episode.EpisodeAction(
            r2_episode.ACTION_NORMALIZE,
            closed,
            close_reason="repair",
        ),
    ]
    monkeypatch.setattr(r2_run, "evaluate_episode", lambda *_, **__: actions)
    runner = r2_run.RodBreakIncidentRunner.__new__(r2_run.RodBreakIncidentRunner)
    runner.source = SimpleNamespace(
        load_raw_buckets=AsyncMock(return_value=[]),
        get_base_moment=AsyncMock(return_value=None),
    )
    runner.incident_repo = _IncidentRepo("R2")
    runner.cursor_repo = _NoopCursors()
    runner._repair_windows = AsyncMock(return_value=[])  # noqa: SLF001
    runner._last_closed_at = AsyncMock(return_value=None)  # noqa: SLF001
    transitions: list[IncidentTransition] = []

    asyncio.run(
        runner._run_station(  # noqa: SLF001
            SimpleNamespace(id=10, well_id=5),
            cursor_ts=T0 - timedelta(hours=1),
            transitions=transitions,
        ),
    )

    assert [(t.kind, t.at) for t in transitions] == [
        (TRANSITION_OPENED, T0),
        (TRANSITION_ESCALATED, escalated),
        (TRANSITION_CLOSED, closed),
    ]
    codes = [build_notification(t).event_code for t in transitions]  # type: ignore[union-attr]
    assert codes == ["r2.load_drop", "r2.possible_rod_break", "r2.closed"]


def test_r9_apply_dates_transitions_by_day_end() -> None:
    runner = r9_run.LoadImbalanceIncidentRunner.__new__(
        r9_run.LoadImbalanceIncidentRunner,
    )
    runner.incident_repo = _IncidentRepo("R9")
    actions = [
        r9_episode.EpisodeAction(r9_episode.ACTION_OPEN_WARNING, YESTERDAY),
        r9_episode.EpisodeAction(
            r9_episode.ACTION_ESCALATE,
            YESTERDAY + timedelta(days=1),
        ),
    ]
    transitions: list[IncidentTransition] = []

    asyncio.run(
        runner._apply(  # noqa: SLF001
            station=SimpleNamespace(id=10, well_id=5),  # type: ignore[arg-type]
            actions=actions,
            verdicts=[],
            active=None,
            now=NOW,
            transitions=transitions,
        ),
    )

    assert [(t.kind, t.at) for t in transitions] == [
        (TRANSITION_OPENED, datetime(2026, 11, 23)),  # noqa: DTZ001
        (TRANSITION_ESCALATED, datetime(2026, 11, 24)),  # noqa: DTZ001
    ]
    assert transitions[0].incident.opened_at == datetime(2026, 11, 22)  # noqa: DTZ001


def test_r10_apply_episode_records_open_escalation_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = r10_run.CitsEventsRunner(_OrderSession([]), now=NOW)  # type: ignore[arg-type]
    runner.incident_repo = _IncidentRepo("R10")  # type: ignore[assignment]
    record = WellRecord(
        well=SimpleNamespace(name="ZHM_0101"),  # type: ignore[arg-type]
        well_id=5,
        abai_id=50,
    )
    event = Event(
        event=EVENT_DROP,
        klass="отклонение",
        last_day=YESTERDAY,
        age_d=1,
        q_last=12.3,
        rezhim=27.0,
        dev=-0.54,
        n_series=3,
        series_from=date(2026, 11, 19),
    )
    transitions: list[IncidentTransition] = []

    async def apply(decision: EpisodeDecision, active: Any) -> None:  # noqa: ANN401
        monkeypatch.setattr(r10_run, "decide", lambda *_, **__: decision)
        await runner._apply_episode(  # noqa: SLF001
            record=record,
            verdict=WellVerdict(event=event),
            active=active,
            ngdu=int(AbaiNGDUIDsEnum.ZHMG),
            day=YESTERDAY,
            now=NOW,
            stats=r10_run.DayStats(),
            intraday=False,
            transitions=transitions,
        )

    seen = datetime(2026, 11, 23)  # noqa: DTZ001
    warning_active = SimpleNamespace(level="warning", last_seen_at=seen)
    alarm_active = SimpleNamespace(level="alarm", last_seen_at=seen)
    asyncio.run(apply(EpisodeDecision(ACTION_UPSERT, level="warning"), None))
    asyncio.run(apply(EpisodeDecision(ACTION_UPSERT, level="alarm"), warning_active))
    # Подтверждение без эскалации — не переход.
    asyncio.run(apply(EpisodeDecision(ACTION_UPSERT, level="alarm"), alarm_active))
    asyncio.run(
        apply(EpisodeDecision(ACTION_CLOSE, close_reason="idle"), alarm_active),
    )

    assert [t.kind for t in transitions] == [
        TRANSITION_OPENED,
        TRANSITION_ESCALATED,
        TRANSITION_CLOSED,
    ]
    # Конец расчётных суток — полночь, он свежее 24 ч к 04:10.
    assert {t.at for t in transitions} == {seen}
    assert {t.well_name for t in transitions} == {"ZHM_0101"}
    assert {t.abai_ngdu_id for t in transitions} == {int(AbaiNGDUIDsEnum.ZHMG)}
    dtos = [build_notification(t) for t in transitions]
    assert [dto.event_code for dto in dtos] == [  # type: ignore[union-attr]
        "r10.liquid_drop",
        "r10.zero_liquid",
        "r10.closed",
    ]
    assert {dto.event_at for dto in dtos} == {NOW}  # type: ignore[union-attr]
    assert dtos[-1].title_ru == "ZHM_0101: поставлен простой в ABAI"  # type: ignore[union-attr]


def test_r10_intraday_transition_is_dated_now() -> None:
    transitions: list[IncidentTransition] = []
    r10_run.CitsEventsRunner._record(  # noqa: SLF001
        transitions,
        TRANSITION_OPENED,
        _state("R10", entity_id=None),
        day=NOW.date(),
        now=NOW,
        ngdu=4,
    )
    r10_run.CitsEventsRunner._record(  # noqa: SLF001
        transitions,
        TRANSITION_OPENED,
        None,
        day=NOW.date(),
        now=NOW,
        ngdu=4,
    )

    assert [t.at for t in transitions] == [NOW]


def _r10_runner(calls: list, cursors: dict[int, Any]) -> Any:  # noqa: ANN401
    runner = r10_run.CitsEventsRunner(_OrderSession(calls), now=NOW, notify=True)  # type: ignore[arg-type]
    runner.cursor_repo = _Cursors(cursors)  # type: ignore[assignment]
    runner.feed_fresh = AsyncMock(return_value=True)  # type: ignore[method-assign]

    async def run_day(
        _ngdu: int,
        _day: date,
        _now: datetime,
        *,
        intraday: bool = False,  # noqa: ARG001
        transitions: list,
    ) -> Any:  # noqa: ANN401
        transitions.append(_t(TRANSITION_OPENED, _state("R10"), at=NOW))
        return r10_run.DayStats()

    runner._run_day = run_day  # type: ignore[method-assign]  # noqa: SLF001
    return runner


ZHMG = int(AbaiNGDUIDsEnum.ZHMG)


def test_r10_daily_run_emits_before_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _patch_emit(monkeypatch, r10_run, calls)
    # Обработано по 21.11 — сегодня считаются 22.11.
    runner = _r10_runner(
        calls,
        {ZHMG: SimpleNamespace(last_event_at=DAY_BEFORE_MIDNIGHT)},
    )

    asyncio.run(runner.run())

    assert calls == [("emit", 1), "commit", ("publish", [101])]


@pytest.mark.parametrize("case", ["fix_date", "no_cursor"])
def test_r10_manual_or_first_run_does_not_emit(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    calls: list = []
    _patch_emit(monkeypatch, r10_run, calls)
    cursors = (
        {}
        if case == "no_cursor"
        else {ZHMG: SimpleNamespace(last_event_at=datetime(2026, 11, 22))}  # noqa: DTZ001
    )
    runner = _r10_runner(calls, cursors)

    asyncio.run(runner.run(fix_date=YESTERDAY if case == "fix_date" else None))

    assert calls == ["commit", ("publish", [])]


def test_r10_intraday_emits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _patch_emit(monkeypatch, r10_run, calls)
    runner = _r10_runner(
        calls,
        {ZHMG: SimpleNamespace(last_event_at=MIDNIGHT)},
    )

    asyncio.run(runner.run(intraday=True))

    assert calls == [("emit", 1), "commit", ("publish", [101])]


# --- Проверка R2: «Подтверждено» ---


def _verify_runner(  # noqa: PLR0913
    monkeypatch: pytest.MonkeyPatch,
    calls: list,
    *,
    dry_run: bool = False,
    recheck: bool = False,
    change: str | None = "new->confirmed",
    evidence_at: datetime = NOW,
    current: Any = None,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    async def fake_emit(_session: Any, **kwargs: Any) -> list[int]:  # noqa: ANN401
        calls.append(("emit", kwargs["incident_id"], kwargs["confirmed_at"]))
        return [7]

    async def fake_publish(ids: Any) -> None:  # noqa: ANN401
        calls.append(("publish", list(ids)))

    monkeypatch.setattr(r2_verify, "emit_r2_confirmed", fake_emit)
    monkeypatch.setattr(r2_verify, "publish_safely", fake_publish)
    monkeypatch.setattr(r2_verify, "local_now", lambda: NOW)
    monkeypatch.setattr(
        r2_verify,
        "evaluate",
        lambda *_: VerificationResult(
            verdict=VERDICT_CONFIRMED,
            reason="repair_rod_break",
            is_final=True,
            evidence_at=evidence_at,
            evidence={"repair": [{"repair_id": 77}]},
        ),
    )
    runner = r2_verify.R2VerificationRunner.__new__(r2_verify.R2VerificationRunner)
    runner.session = _OrderSession(calls)
    runner.dry_run = dry_run
    runner.recheck = recheck
    runner.notify = True
    runner.source = SimpleNamespace(load=AsyncMock(return_value=None))
    runner.verifications = SimpleNamespace(
        get_map_by_incident_ids=AsyncMock(
            return_value={42: current} if current is not None else {},
        ),
    )
    runner._targets = AsyncMock(  # noqa: SLF001
        return_value=[IncidentRef(42, "R2", 5, 10, T0)],
    )
    runner._save = AsyncMock(return_value=change)  # noqa: SLF001
    return runner


def test_verify_live_run_emits_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    runner = _verify_runner(monkeypatch, calls)

    asyncio.run(runner.run())

    assert calls == [("emit", 42, NOW), "commit", ("publish", [7])]


@pytest.mark.parametrize(
    ("kwargs", "runner_kwargs"),
    [
        ({"incident_ids": [42]}, {}),
        ({"backfill_days": 60}, {}),
        ({"recheck_days": 60}, {"recheck": True}),
        ({}, {"dry_run": True}),
        ({}, {"change": None}),
    ],
)
def test_verify_rebuilds_do_not_emit(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict,
    runner_kwargs: dict,
) -> None:
    calls: list = []
    runner = _verify_runner(monkeypatch, calls, **runner_kwargs)

    asyncio.run(runner.run(**kwargs))

    assert not [call for call in calls if call[0] == "emit"]
    assert calls[0] in ("commit", "rollback")


def _old_rule_mark() -> Any:  # noqa: ANN401
    return SimpleNamespace(
        id=1,
        verdict=VERDICT_PENDING,
        reason=None,
        is_final=False,
        evidence_at=None,
        decided_at=T0,
        final_at=None,
        evidence={},
        rule_version="r2-verify-v0",
    )


def test_verify_fresh_confirmation_of_old_rule_mark_emits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    runner = _verify_runner(
        monkeypatch,
        calls,
        evidence_at=NOW - timedelta(hours=3),
        current=_old_rule_mark(),
    )

    asyncio.run(runner.run())

    assert calls == [("emit", 42, NOW), "commit", ("publish", [7])]


def test_verify_old_evidence_does_not_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    runner = _verify_runner(
        monkeypatch,
        calls,
        evidence_at=NOW - timedelta(days=10),
        current=_old_rule_mark(),
    )

    asyncio.run(runner.run())

    assert calls == ["commit", ("publish", [])]


# --- CLI не шлёт, celery-таски шлют ---


class _Runner:
    """Подменяет раннер: запоминает, с каким notify его создали."""

    created: ClassVar[list[bool]] = []

    def __init__(self, _session: Any, **kwargs: Any) -> None:  # noqa: ANN401
        _Runner.created.append(kwargs.get("notify", False))

    async def run(self, *_: Any, **__: Any) -> None:  # noqa: ANN401
        return None


@asynccontextmanager
async def _fake_session() -> AsyncIterator[None]:
    yield None


@pytest.mark.parametrize(
    ("module", "runner_name"),
    [
        (r2_run, "RodBreakIncidentRunner"),
        (r9_run, "LoadImbalanceIncidentRunner"),
        (r10_run, "CitsEventsRunner"),
        (r2_verify, "R2VerificationRunner"),
    ],
)
def test_cli_main_does_not_notify(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,  # noqa: ANN401
    runner_name: str,
) -> None:
    _Runner.created = []
    monkeypatch.setattr(module, runner_name, _Runner)
    monkeypatch.setattr(module, "session_makers", {"app": _fake_session})

    asyncio.run(module.main())

    assert _Runner.created == [False]


@pytest.mark.parametrize(
    ("module", "task_name", "kwargs"),
    [
        (r2_run, "run_rod_break_incidents", {"entity_ids": [10]}),
        (r9_run, "run_load_imbalance_incidents", {"entity_ids": None}),
        (r10_run, "run_cits_events_incidents", {"intraday": True}),
        (r2_verify, "verify_rod_break_incidents", {}),
    ],
)
def test_celery_tasks_notify(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,  # noqa: ANN401
    task_name: str,
    kwargs: dict,
) -> None:
    seen: list[dict] = []

    async def fake_main(*_: Any, **main_kwargs: Any) -> None:  # noqa: ANN401
        seen.append(main_kwargs)

    monkeypatch.setattr(module, "main", fake_main)
    monkeypatch.setattr(module, "run_async", asyncio.run)

    getattr(module, task_name).run(**kwargs)

    assert [call["notify"] for call in seen] == [True]
