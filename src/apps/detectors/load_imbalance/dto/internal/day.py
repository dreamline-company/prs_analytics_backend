from dataclasses import dataclass
from datetime import date

# Состояния суток. Различать «не определено» и «чисто» обязательно: закрывать
# инцидент можно только по явно здоровым суткам, а не по любым несработавшим.
# Иначе закроемся на скважине с K=0.17 — вдвое хуже здоровой (норма 0.02–0.08),
# просто не дотянувшей до порога, — или на сутках, где не было данных.
STATE_UNDETERMINED = "undetermined"  # мало снимков или не хватает фона
STATE_ALERT = "alert"  # сработала хотя бы одна ветвь
STATE_GREY = "grey"  # тревоги нет, но и до нормы не вернулось
STATE_CLEAN = "clean"  # K ниже порога деградации — здоровые сутки

# Ветви правила.
BRANCH_ABSOLUTE = "abs"
BRANCH_RELATIVE = "rel"
BRANCH_LOAD_LOSS = "loss"


@dataclass(frozen=True, slots=True)
class DayAggregate:
    """Суточное распределение момента по одной станции.

    Считается в SQL по снимкам при работающем приводе (ворота по регистру
    скорости), после разворота uint16 и дедупа снимков по ``savetime``.

    Attributes:
        day: Календарные сутки источника.
        n_samples: Сколько снимков при работающем приводе попало в сутки.
        p5: Характерный обратный (генераторный) пик — ход вниз.
        p50: Медиана нагрузки. В правило не входит, идёт в payload.
        p95: Характерный рабочий пик — ход вверх.
    """

    day: date
    n_samples: int
    p5: float
    p50: float
    p95: float

    @property
    def k(self) -> float | None:
        """K = |P5| / P95 — коэффициент перекоса нагрузки.

        ``None``, когда рабочего момента нет вовсе (P95 <= 0): дробь
        вырождается. Такие сутки ловит ветвь потери нагрузки — напрямую по
        P95, а не через K.
        """
        if self.p95 <= 0:
            return None
        return abs(self.p5) / self.p95


@dataclass(frozen=True, slots=True)
class Baseline:
    """Скользящий фон скважины — её собственные последние валидные сутки.

    Attributes:
        n_days: Сколько валидных суток попало в фон.
        k_p90: P90 распределения фоновых K — база относительной ветви.
        p95_median: Медиана фоновых P95 — база ветви потери нагрузки.
    """

    n_days: int
    k_p90: float
    p95_median: float | None


@dataclass(frozen=True, slots=True)
class DayVerdict:
    """Решение правила по одним суткам — самодостаточно для payload."""

    day: date
    state: str
    n_samples: int
    p5: float
    p50: float
    p95: float
    k: float | None
    branches: tuple[str, ...]
    baseline: Baseline | None
