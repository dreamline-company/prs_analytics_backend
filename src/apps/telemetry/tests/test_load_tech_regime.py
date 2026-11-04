"""Техрежимы: правки ABAI и пропущенные строки доходят до копии."""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from apps.telemetry.tasks.load_tech_regime.load_regime import ABAILoadTechRegime
from shared.integrations.abai.models import TechModeProdOil

SINCE = date(2026, 9, 5)
OPEN = date(3333, 12, 31)
WELL = 7


def _src(
    abai_id: int,
    *,
    oil: float = 5.0,
    well: int = WELL,
    dend: date | None = OPEN,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=abai_id,
        well=well,
        dbeg=date(2026, 10, 1),
        dend=dend,
        oil=oil,
        liquid=20.0,
    )


def _local(abai_id: int, *, oil: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(
        id=abai_id * 10,
        abai_id=abai_id,
        abai_well_id=WELL,
        start_date=date(2026, 10, 1),
        end_date=OPEN,
        oil=oil,
        liquid=20.0,
    )


def _run(
    source: list,
    local: list,
    absent_lookup: list | None = None,
) -> tuple[tuple[int, int, int], MagicMock]:
    loader = ABAILoadTechRegime()
    loader.known_wells = {WELL}
    abai = MagicMock(
        model=TechModeProdOil,
        get_list=AsyncMock(side_effect=[source, absent_lookup or []]),
    )
    by_id = {row.abai_id: row for row in local}
    app = MagicMock(
        get_list=AsyncMock(return_value=local),
        list_by_abai_ids=AsyncMock(
            side_effect=lambda ids: [by_id[i] for i in ids if i in by_id],
        ),
        bulk_create=AsyncMock(),
        update_by_id=AsyncMock(),
        delete_by_id=AsyncMock(),
    )
    counts = asyncio.run(loader._reread(abai, app, since=SINCE))  # noqa: SLF001
    return counts, app


def test_edited_plan_and_missing_regimes_reach_the_copy() -> None:
    source = [
        _src(1),  # без изменений
        _src(2, oil=9.5),  # технологи поправили план
        _src(3),  # пропущен keyset-ом
        _src(4, well=999),  # скважины нет у нас
        _src(5, dend=None),  # без даты окончания
    ]
    (created, updated, deleted), app = _run(source, [_local(1), _local(2)])

    assert (created, updated, deleted) == (1, 1, 0)
    assert [dto.abai_id for dto in app.bulk_create.await_args.args[0]] == [3]
    local_id, dto = app.update_by_id.await_args.args
    assert (local_id, dto.oil) == (20, 9.5)


def test_regime_closed_earlier_in_abai_is_updated_not_deleted() -> None:
    closed = _src(1, dend=date(2026, 8, 1))  # вне выборки по dend — дочитан по id
    (_created, updated, deleted), _app = _run([], [_local(1)], absent_lookup=[closed])

    assert (updated, deleted) == (1, 0)


def test_mass_disappearance_deletes_nothing() -> None:
    local = [_local(i) for i in range(1, 101)]

    (_c, _u, deleted), app = _run([], local)

    assert deleted == 0
    app.delete_by_id.assert_not_awaited()
