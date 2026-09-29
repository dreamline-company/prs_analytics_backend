"""Тесты загрузчика WinCC: имя скважины по коду ЦИТС и защита от дублей."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.telemetry.tasks.load_telemetry.load_wincc import (
    WinccLoadTelemetry,
    wincc_well_name,
)

T0 = datetime(2026, 9, 18, 5, 5, 0, 183000)  # noqa: DTZ001


def test_well_name_from_cits_codes() -> None:
    # ЦИТС ЖМГ пишет месторождение ZHT как ZNT, с хвостовыми пробелами.
    assert wincc_well_name("ZNT       ", "112") == "ZHT_0112"
    assert wincc_well_name("UZK", " 377 ") == "UZK_0377"
    # Длинный код — последние три буквы, как раньше.
    assert wincc_well_name("SUAZ", "12") == "UAZ_0012"
    # Буквенный номер — как есть.
    assert wincc_well_name("uvn ", "12G") == "UVN_12G"


def test_well_name_rejects_bad_field_code() -> None:
    with pytest.raises(ValueError, match="3 letters"):
        wincc_well_name("Z1", "12")


class _Repo:
    def __init__(self) -> None:
        self.rows = []

    async def bulk_create(self, data: list) -> None:
        self.rows.extend(data)


def _tm(field: str, well: str, when: datetime, qv: float | None = 10.0) -> object:
    return SimpleNamespace(
        Oil_field=field,
        Well=well,
        Meas_date=when,
        Qv_liq=qv,
        Qm_oil=None,
        Qv_water=None,
        Qm_water=None,
    )


def test_save_skips_loaded_and_repeated_rows_and_unknown_wells() -> None:
    repo = _Repo()
    wells = {"ZHT_0112": 1, "UZK_0377": 2}
    existing = {(2, T0)}  # уже загружен на прошлом прогоне
    batch = [
        _tm("ZNT       ", "112", T0),
        _tm("ZNT       ", "112", T0),  # повтор на границе батчей
        _tm("UZK", "377", T0),
        _tm("BLG", "80", T0),  # скважины нет у нас
    ]

    saved = asyncio.run(
        WinccLoadTelemetry._save_tm(batch, wells, 11, repo, existing),  # noqa: SLF001
    )

    assert saved == 1
    assert [(row.well_id, row.date_time) for row in repo.rows] == [(1, T0)]
    assert existing == {(1, T0), (2, T0)}
    # NULL остаётся NULL — событию 4 R10 нужен именно пустой замер.
    pending = [_tm("UZK", "377", datetime(2026, 9, 19), qv=None)]  # noqa: DTZ001
    asyncio.run(WinccLoadTelemetry._save_tm(pending, wells, 11, repo, existing))  # noqa: SLF001
    assert repo.rows[-1].qv_liquid is None
