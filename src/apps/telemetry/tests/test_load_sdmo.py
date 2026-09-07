"""Чистые функции загрузчика SDMO: разворот строки и выбор станций."""

from datetime import date, datetime
from types import SimpleNamespace

from apps.telemetry.models.sdmo import SDMO_REGISTERS, SdmoStation
from apps.telemetry.repositories.sdmo import FC_DATA_COPY_COLUMNS
from apps.telemetry.tasks.load_sdmo import loader

# savetime в БД хранится наивным UTC — как и в источнике.
_SAVETIME = datetime(2026, 9, 1, 12, 0)  # noqa: DTZ001
_MIDNIGHT = datetime(2026, 9, 1)  # noqa: DTZ001


def _station(*, station_id: int, abai_ngdu_id: int, sdmo_id: int) -> SdmoStation:
    return SdmoStation(id=station_id, abai_ngdu_id=abai_ngdu_id, sdmo_id=sdmo_id)


def test_to_record_uses_local_station_key_not_source_station_id() -> None:
    # У двух НГДУ станция 7 — разные объекты; в строку идёт локальный id и НГДУ.
    station = _station(station_id=5007, abai_ngdu_id=9, sdmo_id=7)
    row = SimpleNamespace(
        id=123,
        station_id=7,
        day=date(2026, 9, 1),
        savetime=_SAVETIME,
        data={"1991": "42.5", "1999": 1, "9999": "ignored"},
    )

    record = loader.to_record(row, station)

    assert len(record) == len(FC_DATA_COPY_COLUMNS)
    assert record[:5] == (123, 5007, 9, date(2026, 9, 1), _SAVETIME)
    registers = dict(zip(SDMO_REGISTERS, record[5:], strict=True))
    assert registers[1991] == 42.5
    assert registers[1999] == 1.0
    assert registers[1998] is None


def test_to_record_merges_packet_list() -> None:
    station = _station(station_id=1, abai_ngdu_id=12, sdmo_id=1)
    row = SimpleNamespace(
        id=1,
        station_id=1,
        day=date(2026, 9, 1),
        savetime=_MIDNIGHT,
        data=[{"1991": 10}, {"1998": 20}, {"1991": 11}],
    )

    registers = dict(
        zip(SDMO_REGISTERS, loader.to_record(row, station)[5:], strict=True),
    )

    assert registers[1991] == 11.0  # поздний пакет перекрывает ранний
    assert registers[1998] == 20.0


def test_select_stations_filters_by_natural_id_within_ngdu() -> None:
    stations = [
        _station(station_id=100, abai_ngdu_id=9, sdmo_id=1),
        _station(station_id=101, abai_ngdu_id=9, sdmo_id=2),
        _station(station_id=102, abai_ngdu_id=9, sdmo_id=3),
    ]

    assert loader.select_stations(stations, None) == stations
    assert [s.id for s in loader.select_stations(stations, [3, 1])] == [100, 102]
    assert loader.select_stations(stations, []) == []


def test_map_well_name_and_resolve() -> None:
    wells = {"VMB_0177": 10, "AKD_0001": 11}

    assert loader.map_well_name("MLD_0177") == "VMB_0177"
    assert loader.map_well_name("AKD_0001") == "AKD_0001"
    assert loader.resolve_well_id(" MLD_0177 ", wells) == 10
    assert loader.resolve_well_id("AKD_0001", wells) == 11
    assert loader.resolve_well_id("XXX_0001", wells) is None
    assert loader.resolve_well_id(None, wells) is None
