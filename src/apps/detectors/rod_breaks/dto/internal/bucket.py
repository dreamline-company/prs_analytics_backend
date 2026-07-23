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
    """

    start_ts: datetime
    mom_min: float
    spd: float
    sample_count: int


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
