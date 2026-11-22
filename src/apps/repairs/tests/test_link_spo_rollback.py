"""Связывание СПО не обрывается на сбое одного ремонта и не плодит дубли.

23.11.2026 на проде: у ремонта 63735 (NRG_001V) на замер 253030 две строки
``repairs_spo`` → ``MultipleResultsFound`` → rollback; следующий ремонт читал
протухший ORM-объект → ``MissingGreenlet`` — и так каждый обход, ремонты
после 63735 крон не видел.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import MissingGreenlet, MultipleResultsFound

from apps.repairs.tasks.fetch_sources import link_spo
from apps.repairs.tasks.fetch_sources.candidates import RepairRef
from apps.repairs.tasks.fill_analytics.fetchers.spo_persist import (
    SpoMeasurementPersister,
)
from shared.integrations.kbrs.api.dtos import (
    MeasurementDetailsDto,
    MeasurementFullDto,
    MeasurementParsedDto,
    MeasurementPassportDto,
)

START = datetime(2026, 10, 30)  # noqa: DTZ001


class _Session:
    """Сессия с поведением ORM: после rollback все загруженные объекты протухли."""

    def __init__(self) -> None:
        self.rolled_back = False
        self.commits = 0

    async def __aenter__(self) -> "_Session":  # noqa: PYI034
        return self

    async def __aexit__(self, *_exc: object) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rolled_back = True


class _OrmRepair:
    """Как ``Repair`` из сессии: чтение атрибута после rollback требует
    подгрузки, а в async-сессии это ``MissingGreenlet``."""

    def __init__(self, session: _Session, **values: object) -> None:
        self._session = session
        self._values = values

    def __getattr__(self, name: str) -> object:
        if self._session.rolled_back:
            msg = "greenlet_spawn has not been called; can't call await_only() here."
            raise MissingGreenlet(msg)
        return self._values[name]


def _orm_repair(session: _Session, repair_id: int) -> _OrmRepair:
    return _OrmRepair(
        session,
        id=repair_id,
        well_id=None,
        abai_well_id=3000004048,
        start_time=START,
        end_time=None,
    )


def test_sweep_goes_on_after_failed_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    candidates = [_orm_repair(session, i) for i in (63735, 63736, 63737)]
    seen: list[RepairRef] = []

    async def list_candidates(*_a: object, **_kw: object) -> list:
        return candidates

    async def link_repair(
        _ctx: object,
        repair: RepairRef,
        **_kw: object,
    ) -> tuple[bool, bool]:
        # Как настоящий: окно и скважина ремонта читаются из его полей.
        seen.append(repair)
        assert (repair.abai_well_id, repair.start_time) == (3000004048, START)
        if repair.id == 63735:
            msg = "Multiple rows were found when one or none was required"
            raise MultipleResultsFound(msg)
        return False, False

    async def schedule(_repair_id: int) -> bool:
        return True

    monkeypatch.setattr(link_spo, "session_makers", {"app": lambda: session})
    monkeypatch.setattr(link_spo, "build_storage", lambda: None)
    monkeypatch.setattr(
        link_spo,
        "_Context",
        lambda s, **_kw: SimpleNamespace(session=s),
    )
    monkeypatch.setattr(link_spo, "list_candidate_repairs", list_candidates)
    monkeypatch.setattr(link_spo, "schedule_repair_analytics", schedule)
    runner = link_spo.LinkRepairSpo()
    monkeypatch.setattr(runner, "_link_repair", link_repair)

    stats = asyncio.run(runner.run())

    assert session.rolled_back
    assert [r.id for r in seen] == [63735, 63736, 63737]
    assert all(isinstance(r, RepairRef) for r in seen)
    assert (stats.processed, stats.failed) == (2, 1)
    assert session.commits == 2


# --- запись СПО: замок до поиска ---------------------------------------------


class _SpoRepo:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def lock_kbrs_measure(self, measure_id: int) -> None:
        self.calls.append(f"lock:{measure_id}")

    async def get_by_kbrs_measure_id(self, measure_id: int, *, well_id: int) -> None:
        self.calls.append(f"by_measure:{measure_id}:{well_id}")

    async def get_by_well_id_and_snapshot_time(self, **_kw: object) -> None:
        self.calls.append("by_snapshot")

    async def create(self, _data: object) -> SimpleNamespace:
        self.calls.append("create")
        return SimpleNamespace(id=1)


class _Storage:
    async def upload_file(self, *_a: object) -> None:
        pass


class _Files:
    def __init__(self) -> None:
        self.next_id = 100

    async def create(self, _data: object) -> SimpleNamespace:
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)


class _Events:
    async def replace_for_spo(self, *_a: object) -> None:
        pass


def full_measurement(start: datetime | None = START) -> MeasurementFullDto:
    return MeasurementFullDto(
        chart=MeasurementParsedDto(
            magic="MS",
            sample_offset=0,
            records_count=0,
            channels=[],
            start=start,
            end=start,
            raw_records=[],
            rows=[],
        ),
        details=MeasurementDetailsDto(
            magic="MS",
            raw_size=3,
            sample_offset=None,
            passport=MeasurementPassportDto(),
        ),
    )


def test_persist_locks_measure_before_looking_up_existing() -> None:
    calls: list[str] = []
    persister = SpoMeasurementPersister(
        storage=_Storage(),  # type: ignore[arg-type]
        file_repo=_Files(),  # type: ignore[arg-type]
        spo_repo=_SpoRepo(calls),  # type: ignore[arg-type]
        spo_event_repo=_Events(),  # type: ignore[arg-type]
    )

    spo = asyncio.run(
        persister.persist(
            well_id=7753,
            measure_id=253030,
            raw_bytes=b"raw",
            full=full_measurement(),
            fallback_snapshot_time=START,
        ),
    )

    assert spo is not None
    assert calls == [
        "lock:253030",
        "by_measure:253030:7753",
        "by_snapshot",
        "create",
    ]
