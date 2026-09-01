"""Постановка генерации заключения в очередь — общий помощник раннеров.

Запуск через брокер: standalone-прогон раннера без Redis не должен падать —
потерянную генерацию догонит подметальщик по отсутствующим заключениям.
"""

from apps.celery_app import celery_app
from core import get_logger

logger = get_logger(__name__)

CONCLUSION_TASK = "detectors.conclusion.generate"


def notify_conclusion(incident_id: int) -> None:
    try:
        celery_app.send_task(CONCLUSION_TASK, kwargs={"incident_id": incident_id})
    except Exception:  # noqa: BLE001
        logger.warning(
            "Conclusion dispatch skipped (broker unavailable); sweep will catch up",
        )
