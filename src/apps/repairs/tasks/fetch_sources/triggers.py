"""Постановка задач ремонтов в очередь из добытчиков, опросчика и API.

Пара «событие + страховочный крон», как у детекторов: событие даёт лаг в
минуты, крон догоняет то, что потерялось при недоступном брокере. Поэтому
все отправки здесь fail-soft — без Redis вызывающий код продолжает работу.
"""

from collections.abc import Sequence

from apps.celery_app import celery_app
from apps.repairs.tasks.fetch_sources.redis_utils import debounce
from core import get_logger

logger = get_logger(__name__)

ANALYTICS_RUN_TASK = "repairs.analytics.run_repair"
ANALYTICS_SWEEP_TASK = "repairs.analytics.sweep"
DOCS_FETCH_TASK = "repairs.fetch.docs"
DYNAMOGRAMS_FETCH_TASK = "repairs.fetch.dynamograms"
TRANSPORT_FETCH_TASK = "repairs.fetch.transport"
SPO_LINK_TASK = "repairs.link_spo"
SPO_TOUCAN_FETCH_TASK = "repairs.fetch.spo_toucan"

# Аналитика одного ремонта запускается не раньше чем через это время после
# первого триггера: файлы из ABAI, СПО и сводки приходят пачками, а каждый
# прогон — LLM-вызовы. Ключ debounce живёт чуть меньше задержки, чтобы
# триггер на границе окна не потерялся, а породил следующий запуск.
ANALYTICS_DEBOUNCE_SEC = 10 * 60
_DEBOUNCE_MARGIN_SEC = 5


def analytics_debounce_key(repair_id: int) -> str:
    return f"repairs:analytics:debounce:{repair_id}"


def _send(task: str, *, kwargs: dict, countdown: int | None = None) -> bool:
    try:
        celery_app.send_task(task, kwargs=kwargs, countdown=countdown)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Task %s not dispatched (broker unavailable); sweep will catch up",
            task,
        )
        return False
    return True


async def schedule_repair_analytics(
    repair_id: int,
    *,
    delay_sec: int = ANALYTICS_DEBOUNCE_SEC,
) -> bool:
    """Поставить аналитику ремонта с debounce. ``True`` — задача поставлена."""
    window = max(delay_sec - _DEBOUNCE_MARGIN_SEC, 1)
    if not await debounce(analytics_debounce_key(repair_id), window_sec=window):
        return False
    return _send(
        ANALYTICS_RUN_TASK,
        kwargs={"repair_id": repair_id},
        countdown=delay_sec,
    )


def schedule_transport_fetch(repair_ids: Sequence[int]) -> bool:
    ids = sorted(set(repair_ids))
    if not ids:
        return False
    return _send(TRANSPORT_FETCH_TASK, kwargs={"repair_ids": ids})


def schedule_spo_link(measure_id: int) -> bool:
    return _send(SPO_LINK_TASK, kwargs={"measure_id": measure_id})
