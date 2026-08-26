from celery.schedules import crontab

from apps.celery_app import celery_app

# Импорт регистрирует celery-задачи детекторов (диспетчер, подметальщик, R2).
from apps.detectors.rod_breaks.tasks.run_incidents.run_incidents import (  # noqa: F401
    run_rod_break_incidents,
)
from apps.detectors.tasks.dispatch.dispatch import (  # noqa: F401
    dispatch_detectors,
    sweep_detectors,
)

# Импорт регистрирует таску инкрементальной загрузки телеметрии SDMO.
from apps.telemetry.tasks.load_sdmo.incremental_load import (  # noqa: F401
    load_sdmo_incremental,
)

# Импорт регистрирует таску инкрементальной загрузки способов эксплуатации.
from apps.wells.tasks.load_well_expl.load_well_expl import (  # noqa: F401
    load_well_expl_incremental,
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
    # Инкрементальная догрузка телеметрии SDMO — каждые 5 минут (источник
    # пишет ~раз в 5 минут; детекторы запускаются по факту прихода данных).
    "telemetry-sdmo-incremental": {
        "task": "telemetry.sdmo.incremental_load",
        "schedule": crontab(minute="*/5"),
    },
    # Страховка детекторов: догнать станции, чьи курсоры отстали от данных
    # (потерянные задачи, первичный прогон истории, включённые правила).
    "detectors-sweep": {
        "task": "detectors.sweep",
        "schedule": crontab(minute="*/15"),
    },
    # Ежедневная догрузка периодов эксплуатации из ABAI (новые id + правки dend).
    "wells-well-expl-incremental": {
        "task": "wells.well_expl.incremental_load",
        "schedule": crontab(hour=5, minute=30),
    },
}
