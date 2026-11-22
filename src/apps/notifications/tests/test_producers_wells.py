"""Скважина: остановка и пуск привода, сбой СДМО — построители и хуки прогона."""

# Времена — местные наивные, как в СДМО и ABAI.
# ruff: noqa: DTZ001, SLF001

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.notifications.producers.common import feed_source_id
from apps.notifications.producers.wells import (
    EVENT_DRIVE_STARTED,
    EVENT_DRIVE_STOPPED,
    ReplaceNotices,
    fmt_duration,
    replace_notifications,
    stop_dedup_key,
)
from apps.wells.services.well_state import refresh
from apps.wells.services.well_state.refresh import NgduResult, WellStateRefresher
from apps.wells.services.well_state.rules import (
    AbaiStatusInput,
    Decision,
    DriveInput,
    RepairInput,
    SdmoInput,
    WellInputs,
    decide,
)
from apps.wells.services.well_state.transitions import OpenState

NOW = datetime(2026, 11, 23, 10, 40)
STOP_AT = datetime(2026, 11, 23, 10, 21, 19)
WORK_SINCE = datetime(2026, 11, 1, 8, 0)
NGDU = 12


def _abai(code: str = "WRK") -> AbaiStatusInput:
    names = {"WRK": "В работе", "DWN": "В простое"}
    reason = "Нет подачи" if code == "DWN" else None
    return AbaiStatusInput(code, names[code], reason, WORK_SINCE)


def _drive(*, running: bool | None, since: datetime, known: bool = True) -> SdmoInput:
    return SdmoInput(
        has_active_station=True,
        drive=DriveInput(
            station_id=5,
            abai_ngdu_id=NGDU,
            running=running,
            since=since,
            known=known,
            last_sample_at=since,
        ),
        last_seen_at=since,
    )


def _decision(
    *,
    running: bool | None,
    since: datetime,
    abai: str | None = "WRK",
    known: bool = True,
) -> Decision:
    inputs = WellInputs(
        abai=_abai(abai) if abai else None,
        sdmo=_drive(running=running, since=since, known=known),
    )
    return decide(inputs, NOW)


def _row(row_id: int, decision: Decision, started_at: datetime) -> OpenState:
    return OpenState(
        id=row_id,
        state=decision.state,
        source=decision.source,
        reason_code=decision.reason_code,
        reason_ru=decision.reason_ru,
        started_at=started_at,
        reason_detail_ru=decision.reason_detail_ru,
        evidence=decision.evidence,
    )


def _working_row(row_id: int = 100) -> OpenState:
    return _row(row_id, _decision(running=True, since=WORK_SINCE), WORK_SINCE)


def _stop_row(row_id: int = 101, abai: str = "WRK") -> OpenState:
    return _row(row_id, _decision(running=False, since=STOP_AT, abai=abai), STOP_AT)


def _replace(closed: OpenState, opened: OpenState) -> ReplaceNotices:
    return replace_notifications(
        well_id=1,
        well_name="VMB_2051",
        abai_ngdu_id=NGDU,
        closed=closed,
        opened=opened,
    )


def _repair_decision(since: datetime) -> Decision:
    inputs = WellInputs(
        abai=_abai("DWN"),
        repair=RepairInput(repair_id=9, type_name="TP 4-1", since=since),
        sdmo=_drive(running=False, since=STOP_AT),
    )
    return decide(inputs, NOW)


# --- Построители ---


def test_stop_with_work_status_is_reported_at_once() -> None:
    notices = _replace(_working_row(), _stop_row())

    assert notices.start_candidate is None
    stop = notices.stopped
    assert stop.event_code == EVENT_DRIVE_STOPPED
    assert stop.severity == "warning"
    assert stop.title_ru == "VMB_2051: привод остановлен"
    assert stop.dedup_key == stop_dedup_key(101) == "well.drive_stopped:well_stop:101"
    assert (stop.source_type, stop.source_id) == ("well_stop", 101)
    assert stop.event_at == STOP_AT
    assert stop.abai_ngdu_id == NGDU
    facts = {fact.label_ru: fact.value_ru for fact in stop.facts}
    assert facts["Стоит с"] == "23.11 10:21"
    assert facts["Статус ABAI"] == "В работе"
    assert facts["Последний отсчёт СДМО"] == "23.11 10:21"
    assert stop.payload["abai_status_code"] == "WRK"
    assert stop.payload["station_id"] == 5


def test_stop_during_abai_downtime_or_without_status_is_not_reported() -> None:
    # Простой в ABAI — остановка уже известна технологам.
    assert _replace(_working_row(), _stop_row(abai="DWN")) == ReplaceNotices()
    no_abai = _decision(running=False, since=STOP_AT, abai=None)
    assert _replace(_working_row(), _row(101, no_abai, STOP_AT)) == ReplaceNotices()


def test_stop_replaced_by_running_drive_is_start_candidate() -> None:
    start_at = STOP_AT + timedelta(minutes=95)
    working = _row(102, _decision(running=True, since=start_at), start_at)

    notices = _replace(_stop_row(), working)

    assert notices.stopped is None
    start = notices.start_candidate
    assert start.event_code == EVENT_DRIVE_STARTED
    assert start.severity == "info"
    assert start.title_ru == "VMB_2051: привод снова работает"
    # Та же цепочка, что у остановки.
    assert (start.source_type, start.source_id) == ("well_stop", 101)
    assert start.dedup_key == "well.drive_started:well_stop:101"
    assert start.event_at == start_at
    facts = {fact.label_ru: fact.value_ru for fact in start.facts}
    assert facts["Простой"] == "1 ч 35 мин"
    assert start.payload["stopped_minutes"] == 95
    # Кандидат и после остановки при простое: решает журнал, не evidence.
    assert _replace(_stop_row(abai="DWN"), working).start_candidate is not None


def test_repair_closed_by_running_drive_is_start_candidate() -> None:
    start_at = STOP_AT + timedelta(hours=5)
    working = _row(102, _decision(running=True, since=start_at), start_at)
    repair = _row(101, _repair_decision(STOP_AT), STOP_AT)
    assert repair.state == "repair"
    assert _replace(repair, working).start_candidate.source_id == 101


def test_no_start_candidate_without_running_drive_or_stop() -> None:
    # Состояние привода не подтверждено — это не пуск.
    unknown = _decision(running=True, since=STOP_AT, known=False)
    assert unknown.state != "stopped"
    assert _replace(_stop_row(), _row(102, unknown, NOW)) == ReplaceNotices()
    # Закрылась не остановка.
    start_at = STOP_AT + timedelta(minutes=5)
    working = _row(102, _decision(running=True, since=start_at), start_at)
    assert _replace(_working_row(), working).start_candidate is None


def test_fmt_duration() -> None:
    assert fmt_duration(timedelta(minutes=25)) == "25 мин"
    assert fmt_duration(timedelta(hours=2)) == "2 ч"
    assert fmt_duration(timedelta(hours=1, minutes=5)) == "1 ч 5 мин"
    assert fmt_duration(timedelta(days=2, hours=3, minutes=7)) == "2 сут 3 ч"
    assert fmt_duration(timedelta(days=1)) == "1 сут"
    assert fmt_duration(timedelta(minutes=-3)) == "0 мин"


# --- Хуки прогона ---


class _States:
    """Журнал состояний в памяти: create отдаёт строку с id."""

    def __init__(self) -> None:
        self.next_id = 500
        self.created: list[Any] = []
        self.updated: list[tuple[int, Any]] = []

    async def create(self, data: Any) -> SimpleNamespace:  # noqa: ANN401
        self.next_id += 1
        row = SimpleNamespace(id=self.next_id, **data.model_dump())
        self.created.append(row)
        return row

    async def update_by_id(self, row_id: int, data: Any) -> None:  # noqa: ANN401
        self.updated.append((row_id, data))


def _refresher(*, notify: bool = True) -> WellStateRefresher:
    refresher = WellStateRefresher(MagicMock(), now=NOW)
    refresher.state_repository = _States()
    refresher.notify = notify
    refresher.well_names = {1: "VMB_2051"}
    return refresher


def _apply(
    refresher: WellStateRefresher,
    open_state: OpenState | None,
    decision: Decision,
    at: datetime = NOW,
) -> OpenState | None:
    return asyncio.run(
        refresher._apply(1, NgduResult(NGDU), open_state, decision, at),
    )


def test_first_row_of_well_is_not_reported() -> None:
    refresher = _refresher()
    _apply(refresher, None, _decision(running=False, since=STOP_AT))
    assert refresher.pending == []


def test_replace_reports_stop_with_id_of_new_row() -> None:
    refresher = _refresher()
    opened = _apply(refresher, _working_row(), _decision(running=False, since=STOP_AT))

    assert [item.event_code for item in refresher.pending] == [EVENT_DRIVE_STOPPED]
    assert refresher.pending[0].source_id == opened.id == 501
    assert refresher.pending[0].event_at == STOP_AT
    assert refresher.start_candidates == {}


def test_rewrite_in_place_is_not_reported() -> None:
    # Начало по данным совпало с началом открытой строки — переписывание.
    refresher = _refresher()
    working = _row(100, _decision(running=True, since=STOP_AT), STOP_AT)
    _apply(refresher, working, _decision(running=False, since=STOP_AT))

    assert refresher.state_repository.created == []
    assert refresher.pending == []


def test_no_notify_no_notifications() -> None:
    refresher = _refresher(notify=False)
    _apply(refresher, _working_row(), _decision(running=False, since=STOP_AT))
    assert refresher.pending == []


class _Journal:
    """Журнал уведомлений в памяти: dedup_key записанных и порядок вызовов."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.calls: list[str] = []

    def written(self, event_code: str) -> list[str]:
        return [key for key in self.keys if key.startswith(event_code + ":")]


class _Session:
    def __init__(self, journal: _Journal) -> None:
        self.journal = journal

    @asynccontextmanager
    async def begin_nested(self) -> Any:  # noqa: ANN401
        yield

    async def commit(self) -> None:
        self.journal.calls.append("commit")

    async def rollback(self) -> None:
        self.journal.calls.append("rollback")


@pytest.fixture
def journal(monkeypatch: pytest.MonkeyPatch) -> _Journal:
    store = _Journal()

    async def emit_all(_session: object, items: list) -> list[int]:
        ids = []
        for item in items:
            store.calls.append(f"emit:{item.event_code}")
            if item.dedup_key not in store.keys:
                store.keys.append(item.dedup_key)
                ids.append(len(store.keys))
        return ids

    async def publish(ids: list[int]) -> None:
        store.calls.append(f"publish:{list(ids)}")

    class _Repository:
        def __init__(self, _session: object) -> None:
            pass

        async def has_dedup_key(self, key: str) -> bool:
            return key in store.keys

    monkeypatch.setattr(refresh, "emit_all", emit_all)
    monkeypatch.setattr(refresh, "publish_notifications", publish)
    monkeypatch.setattr(refresh, "NotificationRepository", _Repository)
    return store


class _Well:
    """Скважина 1 через прогоны: открытая строка переходит между ними."""

    def __init__(self, journal: _Journal, open_state: OpenState) -> None:
        self.refresher = _refresher()
        self.refresher.session = _Session(journal)
        self.open = open_state

    def run(self, *steps: tuple[Decision, datetime]) -> None:
        """Один прогон: смены по порядку и commit."""

        async def work() -> None:
            for decision, at in steps:
                self.open = await self.refresher._apply(
                    1,
                    NgduResult(NGDU),
                    self.open,
                    decision,
                    at,
                )

        asyncio.run(
            self.refresher._in_transaction(NgduResult(NGDU), work(), dry_run=False),
        )


START_AT = STOP_AT + timedelta(hours=2)


def test_chain_a_start_after_stop_rewritten_to_abai_downtime(
    journal: _Journal,
) -> None:
    well = _Well(journal, _working_row())
    well.run((_decision(running=False, since=STOP_AT), STOP_AT))
    # В ABAI поставили простой — строка остановки правится на месте.
    well.run((_decision(running=False, since=STOP_AT, abai="DWN"), NOW))
    assert well.open.evidence["abai"]["code"] == "DWN"
    well.run((_decision(running=True, since=START_AT), START_AT))

    assert journal.keys == [
        "well.drive_stopped:well_stop:501",
        "well.drive_started:well_stop:501",
    ]


def test_chain_b_no_lonely_start_after_unreported_stop(journal: _Journal) -> None:
    well = _Well(journal, _working_row())
    well.run((_decision(running=False, since=STOP_AT, abai="DWN"), STOP_AT))
    # Простой в ABAI сменился на WRK — правка на месте, остановки не было.
    well.run((_decision(running=False, since=STOP_AT), NOW))
    well.run((_decision(running=True, since=START_AT), START_AT))

    assert journal.keys == []
    assert well.refresher.start_candidates == {}


def test_chain_stop_rewritten_to_repair_then_start(journal: _Journal) -> None:
    well = _Well(journal, _working_row())
    well.run((_decision(running=False, since=STOP_AT), STOP_AT))
    # Утром пришёл ремонт с 10:21:00 — та же строка переписана в ремонт.
    well.run((_repair_decision(STOP_AT.replace(second=0)), NOW))
    assert (well.open.id, well.open.state) == (501, "repair")
    well.run((_decision(running=True, since=START_AT), START_AT))

    assert journal.written(EVENT_DRIVE_STARTED) == ["well.drive_started:well_stop:501"]


def test_stop_and_start_in_one_batch(journal: _Journal) -> None:
    well = _Well(journal, _working_row())
    start_at = STOP_AT + timedelta(minutes=12)
    well.run(
        (_decision(running=False, since=STOP_AT), STOP_AT),
        (_decision(running=True, since=start_at), start_at),
    )

    assert journal.calls == [
        "emit:well.drive_stopped",
        "emit:well.drive_started",
        "commit",
        "publish:[1, 2]",
    ]
    assert well.refresher.pending == []
    assert well.refresher.start_candidates == {}


def _transaction(
    refresher: WellStateRefresher,
    *,
    dry_run: bool = False,
    fail: bool = False,
) -> None:
    async def work() -> None:
        refresher.pending.append(_replace(_working_row(), _stop_row()).stopped)
        if fail:
            msg = "boom"
            raise RuntimeError(msg)

    asyncio.run(refresher._in_transaction(NgduResult(NGDU), work(), dry_run=dry_run))


def test_emit_right_before_commit_publish_after(journal: _Journal) -> None:
    refresher = _refresher()
    refresher.session = _Session(journal)
    _transaction(refresher)

    assert journal.calls == ["emit:well.drive_stopped", "commit", "publish:[1]"]
    assert refresher.pending == []


def test_dry_run_and_failed_transaction_send_nothing(journal: _Journal) -> None:
    refresher = _refresher()
    refresher.session = _Session(journal)
    _transaction(refresher, dry_run=True)
    _transaction(refresher, fail=True)

    assert journal.calls == ["rollback", "rollback"]
    assert refresher.pending == []


def test_run_dry_run_disables_notify() -> None:
    refresher = WellStateRefresher(MagicMock(), now=NOW)

    async def no_orgs() -> list:
        return []

    async def no_runs() -> dict:
        return {}

    refresher.org_repository = SimpleNamespace(list_all=no_orgs)
    refresher.run_repository = SimpleNamespace(get_map=no_runs)
    refresher.state_repository = SimpleNamespace(list_open_refs=no_orgs)
    refresher.session = SimpleNamespace(commit=no_orgs, rollback=no_orgs)
    asyncio.run(refresher.run(dry_run=True, notify=True))
    assert refresher.notify is False


# --- Сбой СДМО по НГДУ ---


def _seen(fresh: int, silent: int) -> list[datetime]:
    return [NOW - timedelta(minutes=3)] * fresh + [NOW - timedelta(hours=2)] * silent


def test_sdmo_outage_on_first_run_of_ngdu_is_not_sent() -> None:
    refresher = _refresher()
    refresher.stored_outage = {}
    since = NOW - timedelta(minutes=20)
    refresher._note_sdmo_feed(NGDU, None, since, _seen(1, 9))
    assert refresher.feed_events == {}


def test_sdmo_outage_goes_into_transaction_of_ngdu_wells() -> None:
    refresher = _refresher()
    refresher.stored_outage = {NGDU: None}
    since = NOW - timedelta(minutes=20)
    refresher._note_sdmo_feed(NGDU, None, since, _seen(1, 9))

    outage = refresher.feed_events[NGDU]
    assert outage.event_code == "feed.sdmo_outage"
    facts = {fact.label_ru: fact.value_ru for fact in outage.facts}
    assert facts["Станций на связи за 20 мин"] == "1 из 10"

    upserts: list[Any] = []

    async def upsert(data: Any) -> None:  # noqa: ANN401
        upserts.append(data)

    refresher.run_repository = SimpleNamespace(upsert=upsert)
    asyncio.run(refresher._refresh_ngdu_wells(NgduResult(NGDU), [], started=0.0))

    assert upserts[0].sdmo_outage_since == refresher.outage_since_by_ngdu.get(NGDU)
    assert refresher.pending == [outage]
    assert refresher.feed_events == {}


def test_sdmo_restored_and_steady_states() -> None:
    refresher = _refresher()
    since = NOW - timedelta(hours=1)
    refresher.stored_outage = {NGDU: since}
    refresher._note_sdmo_feed(NGDU, since, since, _seen(1, 9))  # сбой длится
    assert refresher.feed_events == {}
    refresher._note_sdmo_feed(NGDU, since, None, _seen(8, 2))
    restored = refresher.feed_events[NGDU]
    assert restored.event_code == "feed.sdmo_restored"
    # Одна цепочка со сбоем.
    assert restored.source_id == feed_source_id(NGDU, since)
