"""Сверка зеркала технологических объектов ABAI (ГЗУ и привязки скважин)."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from apps.wells.dto.internal.repositories.tech import TechTypeDTO, WellTechDTO
from apps.wells.tasks.load_tech.load_tech import (
    LoadTech,
    SourceShrunkError,
    plan_sync,
    tech_dto,
    tech_history_dto,
    well_tech_dto,
)
from shared.integrations.abai.repositories.techs import ABAITechRepository

OPEN_LINK_END = date(3333, 12, 31)


def link(**kwargs: object) -> WellTechDTO:
    values = {
        "abai_id": 1,
        "abai_well_id": 10,
        "abai_tech_id": 100,
        "tap": 7,
        "dbeg": date(2002, 5, 22),
        "dend": OPEN_LINK_END,
    }
    return WellTechDTO(**(values | kwargs))


def test_plan_sync_creates_updates_and_deletes() -> None:
    local = [
        SimpleNamespace(**link(abai_id=1).model_dump()),
        SimpleNamespace(**link(abai_id=2).model_dump()),
        SimpleNamespace(**link(abai_id=3).model_dump()),
    ]
    # 1 без изменений, 2 — скважину перевели на другой отвод, 3 удалили в
    # источнике, 4 — новая привязка.
    source = [link(abai_id=1), link(abai_id=2, tap=9), link(abai_id=4)]

    plan = plan_sync(source, local)

    assert [dto.abai_id for dto in plan.create] == [4]
    assert [dto.abai_id for dto in plan.update] == [2]
    assert plan.update[0].tap == 9
    assert plan.delete == [3]


def test_plan_sync_on_empty_copy_creates_everything() -> None:
    source = [TechTypeDTO(abai_id=2, name_short_ru="ГЗУ", code="GMS")]

    plan = plan_sync(source, [])

    assert plan.create == source
    assert plan.update == []
    assert plan.delete == []


def test_tech_without_known_type_is_kept_without_type() -> None:
    def type_of(tech_type: int | None) -> int | None:
        return tech_dto(
            SimpleNamespace(id=5, tech_type=tech_type),
            {2},
        ).abai_tech_type_id

    assert type_of(2) == 2
    assert type_of(99) is None
    assert type_of(None) is None


def test_tech_history_of_unknown_tech_is_skipped() -> None:
    row = SimpleNamespace(
        id=1,
        tech=100,
        parent=555,
        name_ru="ГЗУ-28_VMB",
        name_short_ru="ГЗУ-28_VMB",
        dbeg=date(2000, 6, 24),
        dend=date(9999, 12, 31),
    )

    dto = tech_history_dto(row, {100})

    assert dto is not None
    assert dto.abai_parent_id == 555
    assert tech_history_dto(row, {200}) is None


def test_well_tech_needs_known_well_and_tech() -> None:
    row = SimpleNamespace(
        id=1,
        well=10,
        tech=100,
        tap=7,
        dbeg=date(2002, 5, 22),
        dend=OPEN_LINK_END,
    )

    dto = well_tech_dto(row, {100}, {10})

    assert dto == link()
    assert well_tech_dto(row, {100}, {11}) is None
    assert well_tech_dto(row, {101}, {10}) is None
    no_well = SimpleNamespace(**(vars(row) | {"well": None}))
    assert well_tech_dto(no_well, {100}, {10}) is None


def test_abai_list_all_reads_every_batch() -> None:
    rows = [SimpleNamespace(id=i) for i in range(1, 6)]

    class FakeRepository(ABAITechRepository):
        BATCH_SIZE = 2

        def __init__(self) -> None:
            self.calls: list[int] = []

        async def list_after_id(self, last_id: int, *, limit: int) -> list:
            self.calls.append(last_id)
            return [row for row in rows if row.id > last_id][:limit]

    repo = FakeRepository()

    assert asyncio.run(repo.list_all()) == rows
    assert repo.calls == [0, 2, 4]


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


class FakeSource:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    async def list_all(self) -> list[SimpleNamespace]:
        return self.rows


class FakeMirror:
    def __init__(self, name: str, log: list[str], rows: list[object]) -> None:
        self.name, self.log, self.rows = name, log, rows

    async def list_all(self) -> list[object]:
        return self.rows

    async def bulk_create(self, data: list[object]) -> None:
        if data:
            self.log.append(f"create {self.name}")

    async def update_by_abai_id(self, data: object) -> None:
        self.log.append(f"update {self.name} {data.abai_id}")  # type: ignore[attr-defined]

    async def delete_by_abai_ids(self, abai_ids: list[int]) -> None:
        if abai_ids:
            self.log.append(f"delete {self.name} {abai_ids}")


def make_loader(
    mirror: dict[str, list[object]],
) -> tuple[LoadTech, FakeSession, list[str]]:
    session = FakeSession()
    log: list[str] = []
    loader = LoadTech(abai_session=None, app_session=session)  # type: ignore[arg-type]
    loader.abai_tech_type_repo = FakeSource(
        [SimpleNamespace(id=2, name_ru=None, name_short_ru="ГЗУ", code="GMS")],
    )
    loader.abai_tech_repo = FakeSource([SimpleNamespace(id=100, tech_type=2)])
    loader.abai_tech_history_repo = FakeSource([])
    loader.abai_well_tech_repo = FakeSource(
        [
            SimpleNamespace(
                id=1,
                well=10,
                tech=100,
                tap=8,
                dbeg=None,
                dend=OPEN_LINK_END,
            ),
        ],
    )
    loader.well_repo = SimpleNamespace(list_abai_ids=FakeSource({10}).list_all)
    for name in ("tech_type", "tech", "tech_history", "well_tech"):
        setattr(loader, f"{name}_repo", FakeMirror(name, log, mirror.get(name, [])))
    return loader, session, log


def stale_link(abai_id: int, tech: int) -> SimpleNamespace:
    return SimpleNamespace(
        **link(abai_id=abai_id, abai_tech_id=tech, dbeg=None).model_dump(),
    )


def test_run_writes_from_types_to_links_and_deletes_backwards() -> None:
    mirror = {
        "tech": [SimpleNamespace(abai_id=101, abai_tech_type_id=None)],
        "well_tech": [stale_link(1, tech=101)],
    }
    loader, session, log = make_loader(mirror)

    asyncio.run(loader.run())

    # Привязку переводят на новую ГЗУ раньше, чем удаляют старую.
    assert log == [
        "create tech_type",
        "create tech",
        "update well_tech 1",
        "delete tech [101]",
    ]
    assert session.calls == ["commit"]


def test_dry_run_rolls_back() -> None:
    loader, session, log = make_loader({})

    asyncio.run(loader.run(dry_run=True))

    assert log
    assert session.calls == ["rollback"]


def test_shrunk_source_aborts_before_any_write() -> None:
    mirror = {"well_tech": [stale_link(i, tech=100) for i in range(1, 4)]}
    loader, session, log = make_loader(mirror)

    with pytest.raises(SourceShrunkError):
        asyncio.run(loader.run())

    assert log == []
    assert session.calls == []
