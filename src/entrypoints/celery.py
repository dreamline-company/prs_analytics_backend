from celery.schedules import crontab

from apps.celery_app import celery_app

# Импорт регистрирует celery-задачи детекторов в приложении (worker/beat).
from apps.detectors.rod_breaks.tasks.run_detection import (  # noqa: F401
    run_rod_break_detection,
)
from core.settings import get_settings

settings = get_settings()
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.TZ_NAME,
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

celery_app.conf.beat_schedule = {
    # Ежедневный прогон детектора обрыва штанги (R2) по флоту type_1900=6.
    # В 06:00 — после ночной загрузки телеметрии (load_sdmo).
    "detectors-rod-breaks-daily": {
        "task": "detectors.rod_breaks.run",
        "schedule": crontab(hour=6, minute=0),
    },
}
