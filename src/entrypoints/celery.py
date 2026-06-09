from celery import Celery

from core.settings import get_settings

settings = get_settings()
broker_url = settings.CELERY_BROKER_REDIS_DB_URL
celery_app = Celery(
    "my_app",
    broker=settings.CELERY_BROKER_REDIS_DB_URL,
    backend=settings.CELERY_BACKEND_REDIS_DB_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


celery_app.conf.beat_schedule = {}
