"""Паспорт привода по последним отсчётам: станции, пишущие регистры
отдельными пакетами, и выбор станции с самым свежим отсчётом."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from apps.telemetry.repositories.sdmo import (
    PUMP_PARAMETERS_LOOKBACK_ROWS,
    SdmoFcDataRepository,
    merge_pump_tail,
)

T = datetime(2026, 11, 22, 17, 54, 47)  # noqa: DTZ001


def row(
    seconds_ago: int,
    *,
    station_id: int = 2601,
    moment: float | None = None,
    speed: float | None = None,
    fill: float | None = None,
) -> dict:
    return {
        "savetime": T - timedelta(seconds=seconds_ago),
        "station_id": station_id,
        "pump_moment": moment,
        "pump_speed": speed,
        "pump_fill": fill,
    }


def test_split_packets_take_last_filled_value_of_each_register() -> None:
    # UVK_0444: скорость, ток и момент приходят отдельными пакетами.
    tail = [
        row(0, speed=140.0),
        row(4),
        row(10),
        row(15),
        row(300, speed=140.0),
        row(304, moment=95.0),
        row(310, moment=96.0, fill=100.0),
    ]
    merged = merge_pump_tail(tail)
    assert merged == {
        "savetime": T,
        "station_id": 2601,
        "pump_moment": 95.0,
        "pump_speed": 140.0,
        "pump_fill": 100.0,
    }


def test_full_row_is_taken_as_is() -> None:
    tail = [row(0, moment=0.0, speed=0.0, fill=0.0), row(120, moment=90.0, speed=140.0)]
    merged = merge_pump_tail(tail)
    assert merged is not None
    # Ноль — значение, а не пропуск: остановка не подменяется моментом до неё.
    assert (merged["pump_moment"], merged["pump_speed"], merged["pump_fill"]) == (
        0.0,
        0.0,
        0.0,
    )


def test_register_missing_in_whole_tail_stays_none() -> None:
    merged = merge_pump_tail([row(0, speed=140.0), row(5, speed=140.0)])
    assert merged is not None
    assert merged["pump_moment"] is None
    assert merged["pump_speed"] == 140.0


def test_empty_tail() -> None:
    assert merge_pump_tail([]) is None


def test_repository_picks_freshest_station_and_reads_tail() -> None:
    repo = SdmoFcDataRepository.__new__(SdmoFcDataRepository)
    repo.get_projection_list = AsyncMock(
        side_effect=[
            [row(600, station_id=1, moment=50.0, speed=100.0)],
            [row(0, station_id=2, speed=140.0), row(5, station_id=2, moment=95.0)],
            [],
        ],
    )
    last = asyncio.run(repo.get_last_pump_parameters_by_stations([1, 2, 3]))
    assert last is not None
    assert last["station_id"] == 2
    assert last["pump_moment"] == 95.0
    assert last["pump_speed"] == 140.0
    spec = repo.get_projection_list.await_args_list[0].args[0]
    assert spec.limit == PUMP_PARAMETERS_LOOKBACK_ROWS


def test_repository_without_data() -> None:
    repo = SdmoFcDataRepository.__new__(SdmoFcDataRepository)
    repo.get_projection_list = AsyncMock(return_value=[])
    assert asyncio.run(repo.get_last_pump_parameters_by_stations([1])) is None
