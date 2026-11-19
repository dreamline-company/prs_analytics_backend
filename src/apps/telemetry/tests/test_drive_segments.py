"""Отрезки состояния привода по коду 1999: разбор по битам, мусор, дубли,
разрывы связи, подписи и сборка ответа по станциям скважины."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.telemetry.dto.queries.sdmo import ListSdmoDriveSegmentsByWellIdQuery
from apps.telemetry.services.drive import VltStatusKind, vlt_status_kind
from apps.telemetry.services.drive_segments import (
    DriveSegment,
    DriveSegmentState,
    build_drive_segments,
    last_valid_row,
    vlt_reason_ru,
)
from apps.telemetry.use_cases.list_sdmo_drive_segments_by_well_id import (
    ListSdmoDriveSegmentsByWellIdUseCase,
    to_local_naive,
)

T0 = datetime(2026, 11, 18, 14, 0)  # noqa: DTZ001
GAP = timedelta(minutes=30)
RUN, STOP, ALARM, NO_DATA = (
    DriveSegmentState.RUN,
    DriveSegmentState.STOP,
    DriveSegmentState.ALARM,
    DriveSegmentState.NO_DATA,
)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _rows(*codes: float | None, step: int = 2) -> list[tuple[datetime, float | None]]:
    return [(_at(i * step), code) for i, code in enumerate(codes)]


type Brief = list[tuple[DriveSegmentState, int | None, int]]


def _brief(segments: list[DriveSegment]) -> Brief:
    return [(segment.state, segment.code, segment.packets) for segment in segments]


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (0, VltStatusKind.STOP),
        (1, VltStatusKind.RUN),
        (2, VltStatusKind.RUN),
        (4, VltStatusKind.RUN),
        (3, VltStatusKind.RUN),  # авто + накопление — на проде у двух станций ЭВН
        # Предупреждения без бита работы: привод вращается.
        (8, VltStatusKind.RUN),
        (32, VltStatusKind.RUN),
        (256, VltStatusKind.RUN),
        (40, VltStatusKind.RUN),
        (24, VltStatusKind.RUN),
        (33, VltStatusKind.RUN),
        (512, VltStatusKind.ALARM),
        (1024, VltStatusKind.ALARM),
        (8193, VltStatusKind.ALARM),  # бит работы при аварии — всё равно авария
        (16384, VltStatusKind.STOP),
        (1048576, VltStatusKind.STOP),
        (128, VltStatusKind.STOP),
    ],
)
def test_vlt_status_kind_by_bits(code: int, kind: VltStatusKind) -> None:
    assert vlt_status_kind(float(code)) is kind


@pytest.mark.parametrize(
    "value",
    [
        None,
        -1.0,
        9999.0,
        9999999.0,
        134250496.0,
        2048.0,  # бит 11 в таблице РЭ не определён
        511.6,  # дробное — не код
        0.5,
        float("nan"),
        float("inf"),
    ],
)
def test_vlt_status_kind_rejects_garbage(value: float | None) -> None:
    assert vlt_status_kind(value) is None


def test_segments_split_by_code_not_by_state() -> None:
    """1 → 32 → 1 — три отрезка «работает»: фронт сам решает, сливать ли их."""
    segments = build_drive_segments(_rows(1, 1, 32, 1, 1), no_data_gap=GAP)

    assert _brief(segments) == [(RUN, 1, 2), (RUN, 32, 1), (RUN, 1, 2)]
    assert segments[0].end_at == segments[1].start_at == _at(4)
    assert segments[1].end_at == segments[2].start_at == _at(6)
    assert segments[-1].end_at == _at(8)


def test_stop_and_alarm_segments() -> None:
    segments = build_drive_segments(_rows(1, 0, 0, 512, 1), no_data_gap=GAP)

    assert _brief(segments) == [(RUN, 1, 1), (STOP, 0, 2), (ALARM, 512, 1), (RUN, 1, 1)]


def test_garbage_and_missing_status_do_not_split_a_segment() -> None:
    segments = build_drive_segments(_rows(1, -1, None, 9999, 1), no_data_gap=GAP)

    assert _brief(segments) == [(RUN, 1, 2)]
    assert (segments[0].start_at, segments[0].end_at) == (_at(0), _at(8))


def test_long_run_of_garbage_becomes_no_data() -> None:
    rows = _rows(1, *([-1] * 20), 0, step=2)  # 40 минут без пригодного статуса
    segments = build_drive_segments(rows, no_data_gap=GAP)

    assert _brief(segments) == [(RUN, 1, 1), (NO_DATA, None, 0), (STOP, 0, 1)]
    assert (segments[1].start_at, segments[1].end_at) == (_at(0), _at(42))


def test_gap_equal_to_threshold_is_not_no_data() -> None:
    rows = [(_at(0), 1.0), (_at(30), 1.0)]

    assert _brief(build_drive_segments(rows, no_data_gap=GAP)) == [(RUN, 1, 2)]


def test_gap_between_same_codes_keeps_both_sides() -> None:
    rows = [(_at(0), 1.0), (_at(2), 1.0), (_at(60), 1.0)]
    segments = build_drive_segments(rows, no_data_gap=GAP)

    assert _brief(segments) == [(RUN, 1, 2), (NO_DATA, None, 0), (RUN, 1, 1)]
    assert segments[0].end_at == _at(2)


def test_duplicate_savetime_last_packet_wins() -> None:
    rows = [(_at(0), 1.0), (_at(2), 1.0), (_at(2), 0.0), (_at(4), 0.0)]

    assert _brief(build_drive_segments(rows, no_data_gap=GAP)) == [
        (RUN, 1, 1),
        (STOP, 0, 2),
    ]


def test_no_usable_rows_give_no_segments() -> None:
    assert build_drive_segments(_rows(None, -1, 9999), no_data_gap=GAP) == []


def test_window_without_rows_is_one_no_data() -> None:
    segments = build_drive_segments(
        _rows(None, -1),
        no_data_gap=GAP,
        window_start=_at(0),
        window_end=_at(600),
    )

    assert _brief(segments) == [(NO_DATA, None, 0)]
    assert (segments[0].start_at, segments[0].end_at) == (_at(0), _at(600))


def test_silence_at_window_edges_is_no_data() -> None:
    """Молчание у краёв окна — по тому же правилу разрыва, что и внутри."""
    rows = [(_at(60), 1.0), (_at(62), 1.0)]
    segments = build_drive_segments(
        rows,
        no_data_gap=GAP,
        window_start=_at(0),
        window_end=_at(600),
    )

    assert _brief(segments) == [(NO_DATA, None, 0), (RUN, 1, 2), (NO_DATA, None, 0)]
    assert (segments[0].start_at, segments[0].end_at) == (_at(0), _at(60))
    assert (segments[-1].start_at, segments[-1].end_at) == (_at(62), _at(600))


def test_short_silence_at_window_edges_is_ignored() -> None:
    rows = [(_at(10), 1.0), (_at(590), 1.0)]
    segments = build_drive_segments(
        rows,
        no_data_gap=GAP,
        window_start=_at(0),
        window_end=_at(600),
    )

    # Разрыв внутри окна — no_data, края (10 мин) — нет.
    assert _brief(segments) == [(RUN, 1, 1), (NO_DATA, None, 0), (RUN, 1, 1)]


def test_old_row_before_window_keeps_state_before_silence() -> None:
    """Давний отсчёт до окна говорит, в каком состоянии станция замолчала:
    stop → no_data → run, событие пуска — на первом отсчёте после разрыва."""
    rows = [(_at(-3 * 24 * 60), 0.0), (_at(60), 1.0)]
    segments = build_drive_segments(
        rows,
        no_data_gap=GAP,
        window_start=_at(0),
        window_end=_at(62),
    )

    assert _brief(segments) == [(STOP, 0, 1), (NO_DATA, None, 0), (RUN, 1, 1)]
    assert segments[1].end_at == segments[2].start_at == _at(60)


def test_last_valid_row_skips_garbage() -> None:
    newest_first = [(_at(4), -1.0), (_at(2), None), (_at(0), 0.0)]

    assert last_valid_row(newest_first) == (_at(0), 0.0)
    assert last_valid_row([(_at(0), -1.0)]) is None


def test_reason_labels_follow_station_type() -> None:
    assert vlt_reason_ru(0, type_1900=1) == "Остановлен"
    assert vlt_reason_ru(512, type_1900=1) == "Заклинивание (STALL)"
    assert vlt_reason_ru(512, type_1900=6) == "Потеря скорости (STALL)"
    # В РЭ ЭВН бита нет, а в данных ЭВН он есть — подпись ШГН.
    assert vlt_reason_ru(1048576, type_1900=6) == "Задержка автоматического перезапуска"
    assert vlt_reason_ru(40, type_1900=1) == "Восстановление (RECOVERY), Парафинизация"
    # У аварии — только аварийные биты.
    assert vlt_reason_ru(8193, type_1900=1) == "Блокировка после отключения (TRIP LOCK)"


def _station(station_id: int, type_1900: int | None = 6) -> SimpleNamespace:
    return SimpleNamespace(id=station_id, name=f"ST_{station_id}", type_1900=type_1900)


def test_use_case_prepends_last_row_before_window() -> None:
    """Состояние на левом краю — последний пригодный отсчёт до окна: остановка
    до окна и работа в окне дают границу, а не пуск из ниоткуда."""
    station_repo = SimpleNamespace(
        list_by_well_id=AsyncMock(return_value=[_station(7)]),
    )
    fc_repo = SimpleNamespace(
        list_vlt_status_before=AsyncMock(
            return_value=[
                {"savetime": _at(-2), "status": -1.0},
                {"savetime": _at(-4), "status": 0.0},
            ],
        ),
        list_vlt_status_by_station_period=AsyncMock(
            return_value=[
                {"savetime": _at(0), "status": 1.0},
                {"savetime": _at(2), "status": 1.0},
            ],
        ),
    )
    use_case = ListSdmoDriveSegmentsByWellIdUseCase(
        sdmo_station_repository=station_repo,  # type: ignore[arg-type]
        sdmo_fc_data_repository=fc_repo,  # type: ignore[arg-type]
    )

    [station] = asyncio.run(
        use_case.execute(
            ListSdmoDriveSegmentsByWellIdQuery(well_id=1, start_time=_at(0)),
            now=_at(10),
        ),
    )

    assert station.station_id == 7
    assert [(s.state, s.code, s.start_at, s.end_at) for s in station.segments] == [
        ("stop", 0, _at(-4), _at(0)),
        ("run", 1, _at(0), _at(2)),
    ]
    assert station.segments[0].reason_ru == "Остановлен"
    assert station.segments[1].reason_ru == "Работа в автоматическом режиме"


def test_use_case_without_start_time_reads_from_the_beginning() -> None:
    station_repo = SimpleNamespace(
        list_by_well_id=AsyncMock(return_value=[_station(9, 1), _station(3, 1)]),
    )
    fc_repo = SimpleNamespace(
        list_vlt_status_before=AsyncMock(),
        list_vlt_status_by_station_period=AsyncMock(return_value=[]),
    )
    use_case = ListSdmoDriveSegmentsByWellIdUseCase(
        sdmo_station_repository=station_repo,  # type: ignore[arg-type]
        sdmo_fc_data_repository=fc_repo,  # type: ignore[arg-type]
    )

    query = ListSdmoDriveSegmentsByWellIdQuery(well_id=1)
    stations = asyncio.run(use_case.execute(query, now=_at(0)))

    assert [station.station_id for station in stations] == [3, 9]
    # Без левой границы и без отсчётов «окна» нет — и no_data тоже.
    assert all(station.segments == [] for station in stations)
    fc_repo.list_vlt_status_before.assert_not_awaited()


def test_use_case_does_not_count_the_future_as_silence() -> None:
    """Конец окна в будущем: молчание считается только до «сейчас»."""
    station_repo = SimpleNamespace(
        list_by_well_id=AsyncMock(return_value=[_station(7)]),
    )
    fc_repo = SimpleNamespace(
        list_vlt_status_before=AsyncMock(return_value=[]),
        list_vlt_status_by_station_period=AsyncMock(
            return_value=[{"savetime": _at(0), "status": 1.0}],
        ),
    )
    use_case = ListSdmoDriveSegmentsByWellIdUseCase(
        sdmo_station_repository=station_repo,  # type: ignore[arg-type]
        sdmo_fc_data_repository=fc_repo,  # type: ignore[arg-type]
    )
    query = ListSdmoDriveSegmentsByWellIdQuery(
        well_id=1,
        start_time=_at(0),
        end_time=_at(24 * 60),
    )

    [station] = asyncio.run(use_case.execute(query, now=_at(20)))
    assert [s.state for s in station.segments] == ["run"]

    [station] = asyncio.run(use_case.execute(query, now=_at(120)))
    assert [(s.state, s.end_at) for s in station.segments] == [
        ("run", _at(0)),
        ("no_data", _at(120)),
    ]


def test_aware_time_is_converted_to_local_naive() -> None:
    aware = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)

    assert to_local_naive(aware) == datetime(2026, 11, 18, 14, 0)  # noqa: DTZ001
    assert to_local_naive(T0) is T0
    assert to_local_naive(None) is None
