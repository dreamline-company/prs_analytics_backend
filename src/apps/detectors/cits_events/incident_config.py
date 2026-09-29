"""Жизненный цикл эпизода R10 и коды суточного среза.

``config.py`` описывает само правило — что происходит со скважиной на дату
фиксации. Здесь надстройка: какие события становятся эпизодами, как они
закрываются, и как называются находки среза.

Скрипт автора эпизодов не знает — он печатает состояние на дату. Эпизод
нужен, чтобы видеть начало, развитие и исход: закрылся ли он возвратом дебита,
статусом простоя в ABAI (технологи отреагировали) или ушёл в хронику.
"""

from apps.detectors.cits_events.rule import (
    SERVICE_CHRONIC_HIGH,
    SERVICE_CHRONIC_LOW,
    SERVICE_NO_REGIME,
    SERVICE_NULL_LIQUID,
    SERVICE_PLAN_MISMATCH,
    SERVICE_REGIME_OUTDATED,
    SERVICE_STATUS_MISMATCH,
)
from shared.constants.ngdu import AbaiNGDUIDsEnum

# НГДУ, по которым считается правило. Автор строил и проверял его на ЦИТС ЖМГ;
# у других НГДУ свои ЦИТС и пороги на них не смотрели.
TARGET_NGDU_IDS = (AbaiNGDUIDsEnum.ZHMG,)

# --- Эпизод ---
# Один сигнал на скважину: «дебит ушёл». Отклонение (событие 1) — warning,
# новая серия нулей (событие 2) — alarm; переход отклонения в нули — эскалация
# того же эпизода, а не новый.
REASON_LIQUID_LOSS = "liquid_loss"

# Причины закрытия сверх общих recovered / stale.
# Серия перестала быть свежей (дольше FRESH_D) — отклонение ушло в хронику и
# в сервисный перечень; ведомость про свежие события.
CLOSE_REASON_CHRONIC = "chronic"
# На скважине поставили простой в ABAI — технологи знают, мониторинг молчит.
CLOSE_REASON_IDLE = "idle"

# Сколько пропущенных суток раннер догоняет за прогон, если beat пропускал.
CATCHUP_DAYS = 7

# --- Находки суточного среза (detectors_finding.kind) ---
FINDING_STALE = "stale"  # событие 3: замер устарел
FINDING_NULL_LIQUID = SERVICE_NULL_LIQUID  # событие 4: пустой замер

# Раздел «запросить замер» — для технологов, остальное — качество данных.
SECTION_MEASURE_REQUEST = "measure_request"
SECTION_DATA_QUALITY = "data_quality"
SECTION_KINDS: dict[str, tuple[str, ...]] = {
    SECTION_MEASURE_REQUEST: (FINDING_STALE,),
    SECTION_DATA_QUALITY: (
        FINDING_NULL_LIQUID,
        SERVICE_CHRONIC_LOW,
        SERVICE_CHRONIC_HIGH,
        SERVICE_PLAN_MISMATCH,
        SERVICE_NO_REGIME,
        SERVICE_REGIME_OUTDATED,
        SERVICE_STATUS_MISMATCH,
    ),
}
