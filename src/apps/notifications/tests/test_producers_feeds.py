"""Источники данных: СДМО, ЦИТС, защита ABAI — решения и хуки загрузчиков."""

# ruff: noqa: DTZ001, SLF001

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.notifications.producers.common import feed_source_id
from apps.notifications.producers.feeds import (
    ABAI_GUARD_REPAIRS,
    ABAI_GUARD_STATUSES,
    EVENT_CITS_RESTORED,
    EVENT_CITS_STALE,
    EVENT_SDMO_OUTAGE,
    EVENT_SDMO_RESTORED,
    CitsLoadResult,
    CitsStaleRef,
    abai_guard_notification,
    cits_feed_notification,
    cits_stale_ref,
    sdmo_feed_notification,
)
from apps.repairs.tasks.load_repairs import update_not_finished_repairs
from apps.repairs.tasks.load_repairs.update_not_finished_repairs import (
    UpdateNotFinishedRepairs,
)
from apps.telemetry.tasks.load_telemetry import load_wincc
from apps.telemetry.tasks.load_telemetry.load_wincc import WinccLoadTelemetry
from apps.wells.tasks.load_well_status import load_well_status
from apps.wells.tasks.load_well_status.load_well_status import LoadWellStatus

NOW = datetime(2026, 11, 23, 2, 44)
KMG = 12

# --- СДМО ---


def test_sdmo_outage_and_restore_share_one_chain() -> None:
    since = datetime(2026, 11, 23, 2, 20)
    outage = sdmo_feed_notification(
        abai_ngdu_id=KMG,
        previous_since=None,
        current_since=since,
        now=NOW,
        reporting=3,
        base=10,
    )
    restored = sdmo_feed_notification(
        abai_ngdu_id=KMG,
        previous_since=since,
        current_since=None,
        now=NOW + timedelta(hours=1),
        reporting=9,
        base=10,
    )

    assert outage.event_code == EVENT_SDMO_OUTAGE
    assert outage.severity == "alarm"
    assert outage.title_ru == "СДМО Кайнармунайгаз: нет данных с 02:20"
    assert outage.well_id is None
    assert outage.abai_ngdu_id == KMG
    assert outage.event_at == since
    assert {f.label_ru: f.value_ru for f in outage.facts}[
        "Станций на связи за 20 мин"
    ] == "3 из 10"

    assert restored.event_code == EVENT_SDMO_RESTORED
    assert restored.severity == "info"
    assert restored.title_ru == "СДМО Кайнармунайгаз: данные снова идут"
    assert restored.event_at == NOW + timedelta(hours=1)
    assert outage.source_id == restored.source_id == feed_source_id(KMG, since)
    assert {f.label_ru: f.value_ru for f in restored.facts}["Длительность"] == (
        "1 ч 24 мин"
    )


def test_sdmo_without_change_sends_nothing() -> None:
    since = datetime(2026, 11, 23, 2, 20)
    for previous, current in ((None, None), (since, since)):
        assert (
            sdmo_feed_notification(
                abai_ngdu_id=KMG,
                previous_since=previous,
                current_since=current,
                now=NOW,
            )
            is None
        )


# --- ЦИТС ---

LAST = datetime(2026, 11, 22, 23, 37)  # 23.11 02:44 у КМГ последний замер


def _cits(
    last: datetime,
    previous: CitsStaleRef | None = None,
    load: CitsLoadResult | None = None,
) -> Any:  # noqa: ANN401
    return cits_feed_notification(
        abai_ngdu_id=KMG,
        last_measure_at=last,
        now=NOW,
        previous=previous,
        load=load,
    )


def _open(last: datetime = LAST, *, restored: bool = False) -> CitsStaleRef:
    return CitsStaleRef(
        source_id=feed_source_id(KMG, last),
        last_measure_at=last,
        restored=restored,
    )


def test_cits_fresh_measurements_send_nothing() -> None:
    assert _cits(NOW - timedelta(minutes=4)) is None
    # «Из будущего» (часы ЦИТС ЖМГ) — просто свежо.
    assert _cits(NOW + timedelta(minutes=30)) is None


def test_cits_stale_after_three_hours() -> None:
    assert _cits(NOW - timedelta(hours=2, minutes=59)) is None
    item = _cits(LAST, load=CitsLoadResult(read=1681, added=0))

    assert item.event_code == EVENT_CITS_STALE
    assert item.severity == "alarm"
    assert item.title_ru == "ЦИТС Кайнармунайгаз: нет замеров с 22.11 23:37"
    assert item.well_id is None
    assert item.abai_ngdu_id == KMG
    assert item.event_at == NOW  # момент обнаружения
    assert item.source_id == feed_source_id(KMG, LAST)
    assert item.payload["last_measure_at"] == LAST.isoformat()
    facts = {f.label_ru: f.value_ru for f in item.facts}
    assert facts["Нет замеров"] == "3 ч 7 мин"
    assert facts["Загрузка"] == "новых замеров 0 (прочитано 1681)"


def test_cits_stale_with_load_error() -> None:
    item = _cits(LAST, load=CitsLoadResult(error="ConnectionError()"))
    assert {f.label_ru: f.value_ru for f in item.facts}["Загрузка"] == (
        "ошибка загрузки"
    )
    assert item.payload["load_error"] == "ConnectionError()"


def test_cits_same_outage_is_not_repeated() -> None:
    # Следующий прогон в тот же сбой.
    assert _cits(LAST, previous=_open()) is None
    # Пришло немного замеров, но порог не догнан — тот же сбой.
    later = LAST + timedelta(minutes=5)  # 23:42 — всё ещё больше 3 ч
    assert _cits(later, previous=_open()) is None


def test_cits_new_outage_after_restore_is_a_new_chain() -> None:
    old = _open(datetime(2026, 11, 20, 4, 0), restored=True)
    item = _cits(LAST, previous=old)
    assert item.event_code == EVENT_CITS_STALE
    assert item.source_id != old.source_id


def test_cits_restored_in_the_chain_of_open_outage() -> None:
    fresh = NOW - timedelta(minutes=5)
    item = _cits(fresh, previous=_open())

    assert item.event_code == EVENT_CITS_RESTORED
    assert item.severity == "info"
    assert item.title_ru == "ЦИТС Кайнармунайгаз: замеры снова идут"
    assert item.source_id == feed_source_id(KMG, LAST)
    assert item.event_at == NOW
    assert item.dedup_key == f"feed.cits_restored:feed_cits:{feed_source_id(KMG, LAST)}"

    # Уже восстановлено — тишина.
    assert _cits(fresh, previous=_open(restored=True)) is None


def test_cits_restore_needs_newer_measurement() -> None:
    previous = _open(NOW - timedelta(minutes=5))
    assert _cits(NOW - timedelta(minutes=5), previous=previous) is None


def test_cits_stale_ref_from_journal_payload() -> None:
    ref = cits_stale_ref(
        source_id=1,
        payload={"last_measure_at": LAST.isoformat()},
        restored=False,
    )
    assert ref.last_measure_at == LAST
    assert (
        cits_stale_ref(source_id=1, payload={}, restored=True).last_measure_at is None
    )


class _AppSession:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def commit(self) -> None:
        self.calls.append("commit")


class _Journal:
    def __init__(self, row: SimpleNamespace | None, keys: set[str]) -> None:
        self.row = row
        self.keys = keys

    async def get_last_feed_row(self, event_code: str, **kwargs: Any) -> Any:  # noqa: ANN401
        assert event_code == EVENT_CITS_STALE
        assert kwargs == {"source_type": "feed_cits", "abai_ngdu_id": KMG}
        return self.row

    async def has_dedup_key(self, key: str) -> bool:
        return key in self.keys


def _patch_wincc(
    monkeypatch: pytest.MonkeyPatch,
    *,
    last: datetime | None,
    row: SimpleNamespace | None = None,
    keys: set[str] | None = None,
) -> SimpleNamespace:
    """Подменяет сессию, репозитории и эмиттер; ``calls`` — порядок вызовов."""
    calls: list[str] = []
    monkeypatch.setitem(load_wincc.session_makers, "app", lambda: _AppSession(calls))
    monkeypatch.setattr(load_wincc, "local_now", lambda: NOW)
    tm = SimpleNamespace(date_time=last) if last is not None else None
    last_by_ngdu = AsyncMock(return_value=tm)
    monkeypatch.setattr(
        load_wincc,
        "TelemetryRepository",
        lambda _s: SimpleNamespace(get_last_by_ngdu_id=last_by_ngdu),
    )
    monkeypatch.setattr(
        load_wincc,
        "NotificationRepository",
        lambda _s: _Journal(row, keys or set()),
    )

    async def emit_all(_session: object, items: list) -> list[int]:
        calls.extend(f"emit:{item.event_code}" for item in items)
        return [1]

    async def publish(ids: list[int]) -> None:
        calls.append(f"publish:{ids}")

    monkeypatch.setattr(load_wincc, "emit_all", emit_all)
    monkeypatch.setattr(load_wincc, "publish_notifications", publish)
    return SimpleNamespace(calls=calls, last_by_ngdu=last_by_ngdu)


def test_wincc_check_emits_stale_before_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = _patch_wincc(monkeypatch, last=LAST)
    loader = WinccLoadTelemetry(notify=True)
    loader.load_results[KMG] = CitsLoadResult(read=1681, added=0)

    asyncio.run(loader._check_cits_feed(KMG))

    assert patched.calls == ["emit:feed.cits_stale", "commit", "publish:[1]"]
    # Сбой — по замерам не позже «сейчас» + час, а не + сутки загрузки:
    # опечатка «из будущего» сбой не прячет.
    patched.last_by_ngdu.assert_awaited_once_with(
        abai_ngdu_id=KMG,
        until=NOW + timedelta(hours=1),
    )


def test_wincc_check_restores_open_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(
        source_id=feed_source_id(KMG, LAST),
        payload={"last_measure_at": LAST.isoformat()},
    )
    calls = _patch_wincc(monkeypatch, last=NOW - timedelta(minutes=3), row=row).calls

    asyncio.run(WinccLoadTelemetry(notify=True)._check_cits_feed(KMG))

    assert calls == ["emit:feed.cits_restored", "commit", "publish:[1]"]


def test_wincc_check_silent_when_restored_or_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = feed_source_id(KMG, LAST)
    row = SimpleNamespace(
        source_id=source,
        payload={"last_measure_at": LAST.isoformat()},
    )
    calls = _patch_wincc(
        monkeypatch,
        last=NOW - timedelta(minutes=3),
        row=row,
        keys={f"feed.cits_restored:feed_cits:{source}"},
    ).calls
    asyncio.run(WinccLoadTelemetry(notify=True)._check_cits_feed(KMG))
    assert calls == []

    calls = _patch_wincc(monkeypatch, last=None).calls
    asyncio.run(WinccLoadTelemetry(notify=True)._check_cits_feed(KMG))
    assert calls == []


def test_wincc_check_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wincc(monkeypatch, last=LAST)
    monkeypatch.setattr(
        load_wincc,
        "TelemetryRepository",
        lambda _s: SimpleNamespace(
            get_last_by_ngdu_id=AsyncMock(side_effect=RuntimeError("db down")),
        ),
    )
    asyncio.run(WinccLoadTelemetry(notify=True)._check_cits_feed(KMG))


def test_history_reload_does_not_notify() -> None:
    assert WinccLoadTelemetry(notify=True, since=datetime(2023, 11, 1)).notify is False
    assert WinccLoadTelemetry().notify is False


def test_wincc_checks_ngdu_even_when_its_load_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setitem(load_wincc.session_makers, "app", _Session)
    monkeypatch.setattr(
        load_wincc,
        "WellRepository",
        lambda _s: SimpleNamespace(get_list=AsyncMock(return_value=[])),
    )
    loader = WinccLoadTelemetry(ngdus=("KMG",), notify=True)
    loader._load_kainar = AsyncMock(side_effect=ConnectionError("WinCC down"))
    checked: list[int] = []

    async def check(ngdu_id: int) -> None:
        checked.append(ngdu_id)

    loader._check_cits_feed = check

    asyncio.run(loader.run())

    assert checked == [KMG]
    assert loader.load_results[KMG].error is not None


# --- Защита ABAI ---


def test_abai_guard_once_per_hour_per_guard() -> None:
    at = datetime(2026, 11, 4, 1, 10)
    first = abai_guard_notification(
        guard=ABAI_GUARD_STATUSES,
        gone=7798,
        checked=7800,
        now=at,
    )
    same_hour = abai_guard_notification(
        guard=ABAI_GUARD_STATUSES,
        gone=7798,
        checked=7800,
        now=at + timedelta(minutes=40),
    )
    repairs = abai_guard_notification(
        guard=ABAI_GUARD_REPAIRS,
        gone=50,
        checked=50,
        now=at,
    )

    assert first.source_id == 20261104011
    assert (
        first.dedup_key
        == same_hour.dedup_key
        == "feed.abai_guard:feed_abai:20261104011"
    )
    assert repairs.source_id == 20261104012
    assert first.severity == "warning"
    assert first.well_id is None
    assert first.abai_ngdu_id is None
    assert first.title_ru == "ABAI: пустой ответ, статусы не удалены"
    assert repairs.title_ru == "ABAI: пропали незавершённые ремонты, ничего не помечено"
    assert {f.label_ru: f.value_ru for f in first.facts}["Пропало"] == "7798 из 7800"


def _status_loader(*, source: list[int], notify: bool = True) -> LoadWellStatus:
    def row(abai_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=abai_id,
            abai_id=abai_id,
            well=7,
            abai_well_id=7,
            status=1,
            reason=None,
            dbeg=datetime(2026, 10, 1),
            dend=datetime(3333, 12, 31),
        )

    loader = LoadWellStatus(
        abai_session=MagicMock(),
        app_session=MagicMock(),
        notify=notify,
    )
    loader.known_well_ids = {7}
    loader.known_type_ids = {1}
    loader.status_repo = MagicMock(
        list_open_intervals=AsyncMock(return_value=[row(i) for i in range(1, 2001)]),
        delete_by_abai_ids=AsyncMock(),
        update_by_abai_id=AsyncMock(),
    )
    loader.abai_status_repo = MagicMock(
        list_by_ids=AsyncMock(
            side_effect=lambda ids: [row(i) for i in ids if i in set(source)],
        ),
    )
    return loader


def test_statuses_guard_notifies_on_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(load_well_status, "local_now", lambda: NOW)
    loader = _status_loader(source=[])
    asyncio.run(loader._refresh_open_intervals(NOW))

    [item] = loader.guard_events
    assert item.payload == {"guard": ABAI_GUARD_STATUSES, "gone": 2000, "checked": 2000}
    assert item.source_id == int(f"{NOW:%Y%m%d%H}1")

    written: list = []

    async def emit_all(_session: object, items: list) -> list[int]:
        written.extend(items)
        return [5]

    monkeypatch.setattr(load_well_status, "emit_all", emit_all)
    assert asyncio.run(loader._emit_guard()) == [5]
    assert written == [item]
    assert loader.guard_events == []


def test_statuses_real_deletion_or_no_notify_is_silent() -> None:
    loader = _status_loader(source=list(range(2, 2001)))
    asyncio.run(loader._refresh_open_intervals(NOW))
    assert loader.guard_events == []

    quiet = _status_loader(source=[], notify=False)
    asyncio.run(quiet._refresh_open_intervals(NOW))
    assert quiet.guard_events == []


def test_repairs_guard_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list = []

    async def emit_all(_session: object, items: list) -> list[int]:
        written.extend(items)
        return [9]

    monkeypatch.setattr(update_not_finished_repairs, "emit_all", emit_all)
    monkeypatch.setattr(update_not_finished_repairs, "local_now", lambda: NOW)
    session = MagicMock()

    def guard(
        loader: UpdateNotFinishedRepairs,
        gone: int,
        checked: int,
        marked: int,
    ) -> list[int]:
        return asyncio.run(
            loader._emit_guard(session, gone=gone, checked=checked, marked=marked),
        )

    notifying = UpdateNotFinishedRepairs(notify=True)
    assert guard(notifying, 3, 50, 0) == []  # единицы — настоящие удаления
    assert guard(notifying, 50, 50, 3) == []  # что-то помечено — не «ничего»
    assert guard(UpdateNotFinishedRepairs(), 50, 50, 0) == []
    assert guard(notifying, 50, 50, 0) == [9]
    assert written[0].payload == {
        "guard": ABAI_GUARD_REPAIRS,
        "gone": 50,
        "checked": 50,
    }
