"""Эмиттер: флаг, страховка от старых событий, идемпотентная вставка."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects import postgresql

from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationFactDTO,
)
from apps.notifications.repositories.notification import NotificationRepository
from apps.notifications.services.emitter import NotificationEmitter

NOW = datetime(2026, 11, 22, 12, 0, 0)  # noqa: DTZ001 — местное наивное


class _NoDbSession:
    """Сессия, которой нельзя пользоваться: эмиттер не должен идти в БД."""

    async def execute(self, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        msg = "DB must not be touched"
        raise AssertionError(msg)


class _CapturingSession:
    """Ловит INSERT и имитирует ON CONFLICT (dedup_key) DO NOTHING."""

    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.keys: set[str] = set()
        self.next_id = 1

    async def execute(self, stmt: Any) -> Any:  # noqa: ANN401
        self.statements.append(stmt)
        params = stmt.compile(dialect=postgresql.dialect()).params
        key = params["dedup_key"]
        session = self

        class _Result:
            def scalar_one_or_none(self) -> int | None:
                if key in session.keys:
                    return None
                session.keys.add(key)
                session.next_id += 1
                return session.next_id - 1

        return _Result()


def _dto(**overrides: Any) -> NotificationCreateDTO:  # noqa: ANN401
    data: dict[str, Any] = {
        "event_code": "system.test",
        "severity": "alarm",
        "title_ru": "Проверка",
        "dedup_key": "system.test:1",
        "event_at": NOW - timedelta(minutes=5),
        "abai_ngdu_id": 12,
    }
    data.update(overrides)
    return NotificationCreateDTO(**data)


def _emit(emitter: NotificationEmitter, session: Any, dto: Any) -> int | None:  # noqa: ANN401
    return asyncio.run(emitter.emit(session, dto))


def test_disabled_flag_makes_emitter_noop() -> None:
    emitter = NotificationEmitter(enabled=False, clock=lambda: NOW)
    assert _emit(emitter, _NoDbSession(), _dto()) is None


def test_event_older_than_24h_is_not_written() -> None:
    emitter = NotificationEmitter(enabled=True, clock=lambda: NOW)
    stale = _dto(event_at=NOW - timedelta(hours=24, minutes=1))
    assert _emit(emitter, _NoDbSession(), stale) is None


def test_same_dedup_key_twice_gives_one_row() -> None:
    emitter = NotificationEmitter(enabled=True, clock=lambda: NOW)
    session = _CapturingSession()
    first = _emit(emitter, session, _dto())
    second = _emit(emitter, session, _dto(title_ru="Повторный прогон"))
    assert first == 1
    assert second is None


def test_insert_is_on_conflict_do_nothing_returning_id() -> None:
    emitter = NotificationEmitter(enabled=True, clock=lambda: NOW)
    session = _CapturingSession()
    _emit(
        emitter,
        session,
        _dto(facts=[NotificationFactDTO(label_ru="Момент", value_ru="42 %")]),
    )
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT (dedup_key) DO NOTHING" in sql
    assert "RETURNING notifications_notification.id" in sql
    assert compiled.params["created_at"] == NOW
    assert compiled.params["abai_ngdu_id"] == 12
    assert compiled.params["payload"] == {
        "facts": [{"label_ru": "Момент", "value_ru": "42 %"}],
    }


def test_aware_event_at_becomes_local_naive() -> None:
    dto = _dto(event_at=datetime(2026, 11, 22, 4, 10, tzinfo=UTC))
    assert dto.event_at == datetime(2026, 11, 22, 9, 10)  # noqa: DTZ001 — Atyrau +5


def test_repository_has_no_update_or_delete_of_journal() -> None:
    """Журнал неизменяемый: своих методов правки у репозитория нет."""
    own = set(vars(NotificationRepository))
    assert not {name for name in own if "update" in name or "delete" in name}
