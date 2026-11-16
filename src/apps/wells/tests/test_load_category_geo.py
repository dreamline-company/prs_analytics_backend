"""Сверка зеркала категорий и горизонтов скважин ABAI."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from apps.wells.dto.internal.repositories.category_geo import WellGeoDTO
from apps.wells.services.abai_mirror_sync import SourceShrunkError
from apps.wells.tasks.load_category_geo.load_category_geo import (
    LoadCategoryGeo,
    category_dto,
    geo_dto,
    well_geo_dto,
)
from shared.integrations.abai.models import WellCategory, WellGeo
from shared.integrations.abai.repositories import ABAIWellGeoRepository

# Даты категорий в ABAI — timestamp без пояса, как и в зеркале.
OPEN_CATEGORY_END = datetime(3333, 12, 31)  # noqa: DTZ001
OPEN_GEO_END = date(9999, 12, 31)
OIL, INJ, HRZ = 13, 5, 4


def abai_category(**kwargs: object) -> SimpleNamespace:
    values = {
        "id": 1,
        "well": 10,
        "category": OIL,
        "dbeg": datetime(2022, 2, 1),  # noqa: DTZ001
        "dend": OPEN_CATEGORY_END,
    }
    return SimpleNamespace(**(values | kwargs))


def abai_link(**kwargs: object) -> SimpleNamespace:
    values = {
        "id": 1,
        "well": 10,
        "geo": 100,
        "dbeg": date(2014, 3, 19),
        "dend": OPEN_GEO_END,
    }
    return SimpleNamespace(**(values | kwargs))


def test_category_needs_known_well_and_type() -> None:
    dto = category_dto(abai_category(), {OIL}, {10})

    assert dto is not None
    assert dto.abai_category_type_id == OIL
    assert dto.dend == OPEN_CATEGORY_END
    assert category_dto(abai_category(), {INJ}, {10}) is None
    assert category_dto(abai_category(), {OIL}, {11}) is None
    assert category_dto(abai_category(well=None), {OIL}, {10}) is None


def test_geo_without_known_type_is_kept_without_type() -> None:
    def type_of(geo_type: int | None) -> int | None:
        row = SimpleNamespace(
            id=63,
            name_ru="J3_I-CE",
            name_short_ru="J3_I-CE",
            geo_type=geo_type,
            field_code=None,
        )
        return geo_dto(row, {HRZ}).abai_geo_type_id

    assert type_of(HRZ) == HRZ
    assert type_of(99) is None
    assert type_of(None) is None


def test_well_geo_needs_known_well_and_geo() -> None:
    dto = well_geo_dto(abai_link(), {100}, {10})

    assert dto == WellGeoDTO(
        abai_id=1,
        abai_well_id=10,
        abai_geo_id=100,
        dbeg=date(2014, 3, 19),
        dend=OPEN_GEO_END,
    )
    assert well_geo_dto(abai_link(), {101}, {10}) is None
    assert well_geo_dto(abai_link(), {100}, {11}) is None
    # Начало неизвестно: из ABAI пришёл NULL вместо даты до нашей эры.
    assert well_geo_dto(abai_link(dbeg=None), {100}, {10}).dbeg is None


@pytest.mark.parametrize(
    ("model", "bound"),
    [(WellGeo, "'1900-01-01'"), (WellCategory, "'1900-01-01 00:00:00'")],
)
def test_abai_period_dates_are_read_through_case(model: type, bound: str) -> None:
    # asyncpg падает на датах до нашей эры, поэтому сырые колонки не читаются.
    attrs = model.__mapper__.attrs
    assert attrs.raw_dbeg.deferred
    assert attrs.raw_dend.deferred

    sql = str(
        select(model).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )
    table = model.__table__.fullname
    columns = sql[: sql.index("FROM")]

    assert columns.count("CASE WHEN") == 2
    assert f"{table}.dbeg < {bound}" in columns
    assert f"{table}.dend < {bound}" in columns
    # Вне CASE сырые даты в SELECT не встречаются.
    assert columns.count(f"{table}.dbeg") == 2
    assert columns.count(f"{table}.dend") == 2


def test_abai_list_all_reads_every_batch() -> None:
    rows = [SimpleNamespace(id=i) for i in range(1, 6)]

    class FakeRepository(ABAIWellGeoRepository):
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
        self.created: list[object] = []

    async def list_all(self) -> list[object]:
        return self.rows

    async def bulk_create(self, data: list[object]) -> None:
        if data:
            self.created.extend(data)
            self.log.append(f"create {self.name}")

    async def update_by_abai_id(self, data: object) -> None:
        self.log.append(f"update {self.name} {data.abai_id}")  # type: ignore[attr-defined]

    async def delete_by_abai_ids(self, abai_ids: list[int]) -> None:
        if abai_ids:
            self.log.append(f"delete {self.name} {abai_ids}")


TABLES = ("category_type", "category", "geo_type", "geo", "well_geo")


def reference(id_: int, code: str) -> SimpleNamespace:
    return SimpleNamespace(id=id_, name_ru=None, name_short_ru=None, code=code)


def abai_geo(id_: int, geo_type: int = HRZ) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        name_ru=f"Г-{id_}",
        name_short_ru=f"Г-{id_}",
        geo_type=geo_type,
        field_code=None,
    )


def make_loader(
    mirror: dict[str, list[object]],
) -> tuple[LoadCategoryGeo, FakeSession, list[str], dict[str, FakeMirror]]:
    session = FakeSession()
    log: list[str] = []
    loader = LoadCategoryGeo(abai_session=None, app_session=session)  # type: ignore[arg-type]
    loader.abai_category_type_repo = FakeSource([reference(OIL, "OIL")])
    loader.abai_category_repo = FakeSource([abai_category()])
    loader.abai_geo_type_repo = FakeSource([reference(HRZ, "HRZ")])
    loader.abai_geo_repo = FakeSource([abai_geo(100)])
    loader.abai_well_geo_repo = FakeSource([abai_link()])
    loader.well_repo = SimpleNamespace(list_abai_ids=FakeSource({10}).list_all)
    mirrors = {name: FakeMirror(name, log, mirror.get(name, [])) for name in TABLES}
    for name, repo in mirrors.items():
        setattr(loader, f"{name}_repo", repo)
    return loader, session, log, mirrors


def stale_link(abai_id: int, geo: int) -> SimpleNamespace:
    return SimpleNamespace(
        **WellGeoDTO(
            abai_id=abai_id,
            abai_well_id=10,
            abai_geo_id=geo,
            dbeg=date(2014, 3, 19),
            dend=OPEN_GEO_END,
        ).model_dump(),
    )


def test_run_writes_both_chains_from_types_and_deletes_backwards() -> None:
    mirror = {
        "geo": [
            SimpleNamespace(
                abai_id=101,
                abai_geo_type_id=HRZ,
                name_ru="Г-101",
                name_short_ru="Г-101",
                field_code=None,
            ),
        ],
        "well_geo": [stale_link(1, geo=101)],
    }
    loader, session, log, _ = make_loader(mirror)

    stats = asyncio.run(loader.run())

    # Скважину переводят на новый горизонт раньше, чем удаляют старый.
    assert log == [
        "create category_type",
        "create category",
        "create geo_type",
        "create geo",
        "update well_geo 1",
        "delete geo [101]",
    ]
    assert session.calls == ["commit"]
    assert stats["well_geo"].updated == 1


def test_duplicate_source_ids_are_written_once() -> None:
    # В emg.geo нет первичного ключа: четыре горизонта лежат там дважды.
    loader, _session, _log, mirrors = make_loader({})
    loader.abai_geo_repo = FakeSource([abai_geo(100), abai_geo(100), abai_geo(200)])

    stats = asyncio.run(loader.run())

    assert [dto.abai_id for dto in mirrors["geo"].created] == [100, 200]
    assert stats["geo"].source == 3
    assert stats["geo"].skipped == 1


def test_foreign_rows_are_counted_as_skipped() -> None:
    loader, _session, _log, _ = make_loader({})
    loader.abai_category_repo = FakeSource(
        [abai_category(), abai_category(id=2, well=11)],
    )
    loader.abai_well_geo_repo = FakeSource(
        [abai_link(), abai_link(id=2, geo=999), abai_link(id=3, well=11)],
    )

    stats = asyncio.run(loader.run())

    assert (stats["category"].source, stats["category"].skipped) == (2, 1)
    assert (stats["well_geo"].source, stats["well_geo"].skipped) == (3, 2)


def test_dry_run_rolls_back() -> None:
    loader, session, log, _ = make_loader({})

    asyncio.run(loader.run(dry_run=True))

    assert log
    assert session.calls == ["rollback"]


def test_shrunk_source_aborts_before_any_write() -> None:
    mirror = {"well_geo": [stale_link(i, geo=100) for i in range(1, 4)]}
    loader, session, log, _ = make_loader(mirror)

    with pytest.raises(SourceShrunkError):
        asyncio.run(loader.run())

    assert log == []
    assert session.calls == []


def test_mass_delete_aborts_unless_forced() -> None:
    # Источник отдал половину копии (порция оборвалась) — порог доли источника
    # проходит, но 64 удаления — это сбой, а не правки.
    mirror = {"well_geo": [stale_link(i, geo=100) for i in range(1, 129)]}
    loader, _session, log, _ = make_loader(mirror)
    loader.abai_well_geo_repo = FakeSource([abai_link(id=i) for i in range(1, 65)])

    with pytest.raises(SourceShrunkError):
        asyncio.run(loader.run())
    assert log == []

    asyncio.run(loader.run(force=True))
    assert "delete well_geo" in log[-1]
