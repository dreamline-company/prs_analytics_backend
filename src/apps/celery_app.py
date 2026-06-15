from celery import Celery

from core.settings import get_settings

settings = get_settings()
broker_url = settings.CELERY_BROKER_REDIS_DB_URL
celery_app = Celery(
    "my_app",
    broker=settings.CELERY_BROKER_REDIS_DB_URL,
    backend=settings.CELERY_BACKEND_REDIS_DB_URL,
)
