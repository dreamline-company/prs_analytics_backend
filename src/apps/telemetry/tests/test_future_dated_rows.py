"""Замеры с датой из будущего не грузятся и не двигают курсор."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.telemetry.tasks.load_telemetry import load_wincc
from apps.telemetry.tasks.load_telemetry.load_wincc import WinccLoadTelemetry

LIMIT = datetime(2026, 11, 6, 12, 0)  # noqa: DTZ001


def _tm(at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        Meas_date=at,
        Well="0281",
        Oil_field="ZHT",
        Qv_liq=10.0,
        Qm_oil=3.0,
        Qv_water=7.0,
        Qm_water=7.0,
    )


def test_wincc_skips_measurements_from_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(load_wincc, "future_limit", lambda: LIMIT)
    repo = SimpleNamespace(bulk_create=AsyncMock())
    well = load_wincc.wincc_well_name("ZHT", "0281")

    added = asyncio.run(
        WinccLoadTelemetry._save_tm(  # noqa: SLF001
            [_tm(LIMIT - timedelta(hours=30)), _tm(datetime(2062, 11, 5))],  # noqa: DTZ001
            {well: 1},
            11,
            repo,
            set(),
        ),
    )

    assert added == 1
    saved = repo.bulk_create.await_args.kwargs["data"]
    assert [row.date_time for row in saved] == [LIMIT - timedelta(hours=30)]
