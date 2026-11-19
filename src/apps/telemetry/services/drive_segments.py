"""Отрезки состояния привода по регистру 1999 — сырьё для пусков и остановок
на графике скважины.

Отрезок — подряд идущие отсчёты станции с одним и тем же кодом статуса.
Пуски, остановки, короткие перезапуски и их склейку строит фронт по границам
отрезков, где меняется ``state``: так способ показа меняется без бэкенда.

Правила разбора (сверены на проде 18.11.2026):

- отсчёт без статуса и с мусорным кодом (−1, 9999, неизвестные биты) — как
  пропуск пакета: отрезок не рвёт и события не даёт;
- дубли по ``savetime`` схлопываются, побеждает последний пакет;
- нет пригодных отсчётов дольше ``no_data_gap`` — отрезок ``no_data`` между
  последним отсчётом до разрыва и первым после него, в том числе у краёв окна;
- граница отрезка — ``savetime`` первого отсчёта с новым кодом: настоящий
  момент смены лежит между этим и предыдущим отсчётом (до ~5 минут).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from apps.telemetry.services.drive import (
    VLT_ALARM_BITS,
    vlt_code,
    vlt_status_kind,
)

# Тип станции (telemetry_sdmo_station.type_1900) с подписями ЭВН; у остальных
# типов — подписи ШГН (тип 1 — станок-качалка, «ход/мин»).
EVN_STATION_TYPES = frozenset({6})

# Подписи битов по РЭ: (ШГН, ЭВН). None — у этого оборудования бита нет в РЭ;
# если он всё же пришёл, берётся подпись другого оборудования (1048576 в РЭ ЭВН
# нет, но в данных ЭВН встречается).
_BIT_LABELS: dict[int, tuple[str | None, str | None]] = {
    1: ("Работа в автоматическом режиме", "Работа в автоматическом режиме"),
    2: ("Накопление (PUMP-OFF)", "Накопление (PUMP-OFF)"),
    4: ("Работа в ручном режиме", "Работа в ручном режиме"),
    8: ("Восстановление (RECOVERY)", "Восстановление (RECOVERY)"),
    16: (None, "Потеря обратной связи с датчиком"),
    32: ("Парафинизация", "Парафинизация"),
    64: ("Тайм-аут шины", "Тайм-аут шины"),
    128: (None, "Продувка от газа/песка"),
    256: ("Потеря контакта с позиционным переключателем", None),
    512: ("Заклинивание (STALL)", "Потеря скорости (STALL)"),
    1024: ("Срыв подачи", "Срыв подачи"),
    4096: ("Штатная неисправность привода", "Штатная неисправность привода"),
    8192: (
        "Блокировка после отключения (TRIP LOCK)",
        "Блокировка после отключения (TRIP LOCK)",
    ),
    16384: ("Автоматический перезапуск", "Автоматический перезапуск"),
    32768: ("Высокое давление, вход 1", "Высокое давление, вход 1"),
    65536: ("Высокое давление, вход 53", "Высокое давление, вход 53"),
    262144: ("Низкое давление, вход 53", "Низкое давление, вход 53"),
    524288: (None, "Накопление в ручном режиме"),
    1048576: ("Задержка автоматического перезапуска", None),
}

_STOPPED_LABEL = "Остановлен"


class DriveSegmentState(StrEnum):
    RUN = "run"
    STOP = "stop"
    ALARM = "alarm"
    NO_DATA = "no_data"


@dataclass(frozen=True, slots=True)
class DriveSegment:
    start_at: datetime
    end_at: datetime
    state: DriveSegmentState
    # Код 1999; None — у отрезка no_data.
    code: int | None
    # Пригодных отсчётов в отрезке (у no_data — 0).
    packets: int


def vlt_reason_ru(code: int, *, type_1900: int | None) -> str:
    """Подпись кода для оператора: у аварии — только аварийные биты, иначе —
    все биты кода по порядку; 0 — «Остановлен»."""
    if code == 0:
        return _STOPPED_LABEL
    bits = code & VLT_ALARM_BITS or code
    evn = type_1900 in EVN_STATION_TYPES
    labels = []
    for bit, (shgn_label, evn_label) in _BIT_LABELS.items():
        if not bits & bit:
            continue
        label = (evn_label or shgn_label) if evn else (shgn_label or evn_label)
        if label is not None:
            labels.append(label)
    return ", ".join(labels)


@dataclass(frozen=True, slots=True)
class _Packet:
    savetime: datetime
    code: int
    state: DriveSegmentState


def _valid_packets(rows: Iterable[tuple[datetime, float | None]]) -> list[_Packet]:
    """Пригодные отсчёты по времени; дубль по ``savetime`` — последний пакет."""
    packets: list[_Packet] = []
    for savetime, raw in rows:
        kind = vlt_status_kind(raw)
        code = vlt_code(raw)
        if kind is None or code is None:
            continue
        packet = _Packet(savetime, code, DriveSegmentState(kind.value))
        if packets and packets[-1].savetime == savetime:
            packets[-1] = packet
        else:
            packets.append(packet)
    return packets


def _segment(first: _Packet, end_at: datetime, packets: int) -> DriveSegment:
    return DriveSegment(
        start_at=first.savetime,
        end_at=end_at,
        state=first.state,
        code=first.code,
        packets=packets,
    )


def _no_data(start_at: datetime, end_at: datetime) -> DriveSegment:
    return DriveSegment(
        start_at=start_at,
        end_at=end_at,
        state=DriveSegmentState.NO_DATA,
        code=None,
        packets=0,
    )


def build_drive_segments(
    rows: Iterable[tuple[datetime, float | None]],
    *,
    no_data_gap: timedelta,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[DriveSegment]:
    """Отрезки состояния привода по отсчётам одной станции.

    ``rows`` — ``(savetime, r_1999)`` по возрастанию времени (при равном
    времени — в порядке загрузки); первым может идти отсчёт до окна. Конец
    отрезка — начало следующего, а перед разрывом и у последнего отрезка — время
    его последнего отсчёта.

    Края окна подчиняются тому же правилу разрыва: молчание дольше
    ``no_data_gap`` от ``window_start`` до первого отсчёта и от последнего
    отсчёта до ``window_end`` — тоже ``no_data``; окно без пригодных отсчётов —
    один ``no_data`` на всё окно.
    """
    packets = _valid_packets(rows)
    if not packets:
        if (
            window_start is not None
            and window_end is not None
            and window_end > window_start
        ):
            return [_no_data(window_start, window_end)]
        return []

    segments: list[DriveSegment] = []
    first = last = packets[0]
    if window_start is not None and first.savetime - window_start > no_data_gap:
        segments.append(_no_data(window_start, first.savetime))

    count = 1
    for packet in packets[1:]:
        if packet.savetime - last.savetime > no_data_gap:
            segments.append(_segment(first, last.savetime, count))
            segments.append(_no_data(last.savetime, packet.savetime))
            first = last = packet
            count = 1
        elif packet.code == first.code:
            last = packet
            count += 1
        else:
            segments.append(_segment(first, packet.savetime, count))
            first = last = packet
            count = 1

    segments.append(_segment(first, last.savetime, count))
    if window_end is not None and window_end - last.savetime > no_data_gap:
        segments.append(_no_data(last.savetime, window_end))
    return segments


def last_valid_row(
    rows_newest_first: Iterable[tuple[datetime, float | None]],
) -> tuple[datetime, float | None] | None:
    """Последний пригодный отсчёт из порции «от новых к старым» — состояние на
    левом краю окна."""
    for savetime, raw in rows_newest_first:
        if vlt_status_kind(raw) is not None:
            return savetime, raw
    return None
