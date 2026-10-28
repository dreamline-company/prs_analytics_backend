"""Автомат состояния привода станции СДМО — чистая логика, без базы.

Смена «работает ↔ не работает» засчитывается после двух пригодных отсчётов
подряд с новым значением; начало нового состояния — первый из них. Порога по
времени нет: одиночный сбойный отсчёт отсеивается, любая остановка дольше
двух отсчётов видна (КМГ — 5–10 минут, ЖМГ — 1–2 минуты).

После перерыва в данных дольше ``NO_LINK_GAP`` прежнее состояние требует
подтверждения: если первый отсчёт после перерыва с ним совпал — оно то же, и
начало сохраняется; если нет — до второго отсчёта состояние неизвестно, а
новое начинается с первого из них. Кандидат на смену перерыв не переживает.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from apps.telemetry.services.drive import drive_running, is_known_vlt, vlt_code

# Станция молчит дольше — «нет связи»; тот же порог — перерыв в данных.
NO_LINK_GAP = timedelta(minutes=60)


@dataclass(slots=True)
class DriveSnapshot:
    """Состояние автомата одной станции (строка ``wells_drive_state``)."""

    running: bool | None = None
    since: datetime | None = None
    pending_running: bool | None = None
    pending_since: datetime | None = None
    reconfirming: bool = False
    last_sample_at: datetime | None = None
    last_seen_at: datetime | None = None

    def is_known(
        self,
        now: datetime,
        *,
        outage_since: datetime | None = None,
    ) -> bool:
        """Можно ли опираться на состояние привода «сейчас».

        Во время сбоя СДМО по НГДУ состояние замораживается: новых отсчётов нет
        ни у кого, и свежесть меряется на начало сбоя — станция, молчавшая и до
        него, известной не становится.
        """
        if self.running is None or self.reconfirming or self.last_sample_at is None:
            return False
        moment = min(now, outage_since) if outage_since is not None else now
        return moment - self.last_sample_at <= NO_LINK_GAP


@dataclass(frozen=True, slots=True)
class DriveSample:
    savetime: datetime
    vlt: float | None  # регистр 1999
    rpm: float | None  # регистр 1998


@dataclass(frozen=True, slots=True)
class DriveChange:
    running: bool
    at: datetime  # первый отсчёт нового состояния


@dataclass(slots=True)
class DriveStats:
    """Счётчики для лога прогона."""

    out_of_order: int = 0
    unknown_vlt: Counter = field(default_factory=Counter)  # код -> отсчётов


def advance(
    snapshot: DriveSnapshot,
    samples: Iterable[DriveSample],
    stats: DriveStats | None = None,
) -> list[DriveChange]:
    """Прогнать отсчёты станции через автомат; вернуть подтверждённые смены.

    Отсчёты обрабатываются по времени. Отсчёт старше уже обработанного
    (пришёл не по порядку) пропускается: историю задним числом автомат не
    переписывает. Первое подтверждение из неизвестного состояния — тоже смена.
    """
    stats = stats if stats is not None else DriveStats()
    changes: list[DriveChange] = []
    for sample in sorted(samples, key=lambda item: item.savetime):
        if snapshot.last_seen_at is None or sample.savetime > snapshot.last_seen_at:
            snapshot.last_seen_at = sample.savetime
        if snapshot.last_sample_at is not None:
            if sample.savetime < snapshot.last_sample_at:
                stats.out_of_order += 1
                continue
            if sample.savetime == snapshot.last_sample_at:
                continue  # дубль отсчёта
        if sample.vlt is not None and not is_known_vlt(sample.vlt):
            stats.unknown_vlt[vlt_code(sample.vlt)] += 1
        value = drive_running(sample.vlt, sample.rpm)
        if value is None:
            continue
        if (
            snapshot.last_sample_at is not None
            and sample.savetime - snapshot.last_sample_at > NO_LINK_GAP
        ):
            _clear_pending(snapshot)
            snapshot.reconfirming = snapshot.running is not None
        snapshot.last_sample_at = sample.savetime
        change = _step(snapshot, value=value, at=sample.savetime)
        if change is not None:
            changes.append(change)
    return changes


def _clear_pending(snapshot: DriveSnapshot) -> None:
    snapshot.pending_running = None
    snapshot.pending_since = None


def _step(snapshot: DriveSnapshot, *, value: bool, at: datetime) -> DriveChange | None:
    if value is snapshot.running:
        # То же состояние; после перерыва — подтверждено, начало прежнее.
        snapshot.reconfirming = False
        _clear_pending(snapshot)
        return None
    if snapshot.pending_running is not value:
        snapshot.pending_running, snapshot.pending_since = value, at
        return None
    snapshot.reconfirming = False
    snapshot.running, snapshot.since = value, snapshot.pending_since
    _clear_pending(snapshot)
    return DriveChange(value, snapshot.since)


# Сколько последних отсчётов прогнать автоматом, чтобы узнать текущее
# состояние привода перед поиском его начала назад по истории.
HISTORY_PROBE = 200


class HistoryScan:
    """Начало текущего состояния привода — назад по истории станции, порциями.

    Первый прогон таски: курсора по отсчётам ещё нет. Текущее состояние
    берётся автоматом по последним ``HISTORY_PROBE`` отсчётам; дальше отсчёты
    просматриваются назад, пока не встретятся два пригодных подряд с другим
    значением — это граница. Автомат прогоняется один раз от границы до
    последнего отсчёта: начало состояния и кандидат на смену получаются теми
    же правилами, что в ``advance``. Без пересчёта на каждую порцию — станция,
    работавшая все 30 суток (ЖМГ — ~43 тыс. отсчётов), просматривается за
    один линейный проход.
    """

    def __init__(self) -> None:
        self.samples: list[DriveSample] = []  # от новых к старым
        self.target: bool | None = None
        self._scanned = 0
        self._opposite = 0
        self.boundary: int | None = None

    def feed(self, chunk: Sequence[DriveSample]) -> bool:
        """Добавить порцию (от новых к старым); ``True`` — граница найдена,
        дальше читать не нужно."""
        self.samples.extend(chunk)
        if self.target is None:
            probe = DriveSnapshot()
            advance(probe, self.samples[:HISTORY_PROBE])
            self.target = probe.running
            if self.target is None:
                return False
        for index in range(self._scanned, len(self.samples)):
            sample = self.samples[index]
            value = drive_running(sample.vlt, sample.rpm)
            if value is None:
                continue
            if value is self.target:
                self._opposite = 0
                continue
            self._opposite += 1
            if self._opposite == 2:  # noqa: PLR2004 — два отсчёта подряд
                self.boundary = index
                break
        self._scanned = len(self.samples)
        return self.boundary is not None

    def snapshot(self) -> DriveSnapshot:
        """Состояние автомата после последнего отсчёта; без границы — начало
        обрезано глубиной поиска."""
        snapshot = DriveSnapshot()
        if not self.samples:
            return snapshot
        end = self.boundary if self.boundary is not None else len(self.samples) - 1
        advance(snapshot, self.samples[: end + 1])
        snapshot.last_seen_at = max(snapshot.last_seen_at, self.samples[0].savetime)
        return snapshot
