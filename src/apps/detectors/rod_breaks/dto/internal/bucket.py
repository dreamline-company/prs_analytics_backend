from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RawBucket:
    """Сырая 2-часовая корзина, посчитанная в SQL из телеметрии.

    Attributes:
        start_ts: Левая граница корзины (date_bin от savetime, UTC).
        mom_min: Минимум момента в корзине (регистр 1991). Ключевой для R2.
        spd: Представитель скорости в корзине — медиана (регистр 1998).
        sample_count: Сколько ~2-минутных отсчётов попало в корзину.
        mom_med: Медиана момента в корзине — для восстановления.
        mom_sum: Сумма момента по всем отсчётам корзины (средний за час).
        work_sum / work_n: Сумма и число отсчётов момента при скорости выше
            ``FAILURE_SPD_ABS`` — для базы эпизода (средний до T0).
    """

    start_ts: datetime
    mom_min: float
    spd: float
    sample_count: int
    mom_med: float | None = None
    mom_sum: float = 0.0
    work_sum: float = 0.0
    work_n: int = 0


@dataclass(frozen=True, slots=True)
class Bucket2h:
    """Корзина, обогащённая скользящей медианой за 24 часа.

    ``mom_med24`` / ``spd_med24`` равны ``None``, если в трейлинг-окне слишком
    мало корзин (< ``MIN_MEDIAN_BUCKETS``) — правило такие корзины пропускает.
    """

    start_ts: datetime
    mom_min: float
    spd: float
    mom_med24: float | None
    spd_med24: float | None
    # Сводки сырых отсчётов корзины (см. RawBucket); у тестовых корзин —
    # по умолчанию «один отсчёт со значением mom_min».
    mom_med: float | None = None
    mom_sum: float | None = None
    mom_n: int = 0
    work_sum: float = 0.0
    work_n: int = 0
