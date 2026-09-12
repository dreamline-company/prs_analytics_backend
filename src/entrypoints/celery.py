from celery.schedules import crontab

from apps.celery_app import celery_app

# Импорт регистрирует celery-задачи детекторов (диспетчер, подметальщик, R2, R9).
from apps.detectors.load_imbalance.tasks.run_incidents.run_incidents import (  # noqa: F401
    run_load_imbalance_incidents,
)
from apps.detectors.rod_breaks.tasks.run_incidents.run_incidents import (  # noqa: F401
    run_rod_break_incidents,
)

# Импорт регистрирует утреннюю сборку суточных ведомостей R2/R9 по НГДУ.
from apps.detectors.tasks.build_daily_sheets.build_daily_sheets import (  # noqa: F401
    build_daily_sheets,
)
from apps.detectors.tasks.dispatch.dispatch import (  # noqa: F401
    dispatch_detectors,
    sweep_detectors,
)

# Импорт регистрирует таску генерации ИИ-заключений по эпизодам R2/R9.
from apps.detectors.tasks.generate_conclusion.generate_conclusion import (  # noqa: F401
    generate_conclusion,
)

# Импорт регистрирует таску последовательной синхронизации оргструктуры.
from apps.org.tasks.sync_org.sync_org import sync_org  # noqa: F401

# Импорт регистрирует таску последовательной синхронизации ремонтов.
from apps.repairs.tasks.sync_repairs.sync_repairs import sync_repairs  # noqa: F401

# Импорт регистрирует таску инкрементальной загрузки телеметрии SDMO.
from apps.telemetry.tasks.load_sdmo.incremental_load import (  # noqa: F401
    load_sdmo_incremental,
)
from apps.telemetry.tasks.load_sdmo.sources import configured_ngdus

# Импорт регистрирует таски инкрементальной загрузки замеров WinCC и
# техрежимов ABAI — источники добычи и плана для сводки НГДУ и матрицы.
from apps.telemetry.tasks.load_tech_regime.load_regime import (  # noqa: F401
    load_tech_regime_incremental,
)
from apps.telemetry.tasks.load_telemetry.load_wincc import (  # noqa: F401
    load_wincc_incremental,
)

# Импорт регистрирует таску инкрементальной загрузки ГДИС из ABAI.
from apps.wells.tasks.load_gdis.load_gdis import load_gdis_incremental  # noqa: F401

# Импорт регистрирует таску инкрементальной загрузки способов эксплуатации.
from apps.wells.tasks.load_well_expl.load_well_expl import (  # noqa: F401
    load_well_expl_incremental,
)

# Импорт регистрирует таску инкрементальной загрузки привязок скважин к оргструктуре.
from apps.wells.tasks.load_well_orgs.load_well_orgs import (  # noqa: F401
    load_well_orgs_incremental,
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
    # Запись на каждый НГДУ с настроенной базой: недоступный или медленный
    # источник не задерживает остальные, наложение прогонов одного НГДУ
    # снимает блокировка внутри таска.
    **{
        f"telemetry-sdmo-incremental-{ngdu.name.lower()}": {
            "task": "telemetry.sdmo.incremental_load",
            "schedule": crontab(minute="*/5"),
            "kwargs": {"abai_ngdu_id": int(ngdu)},
        }
        for ngdu in configured_ngdus()
    },
    # Замеры дебитов из WinCC четырёх НГДУ — раз в час (замер на скважину
    # приходит раз в 2–3 суток, чаще опрашивать смысла нет). Сводка НГДУ
    # считает добычу по замерам не старше 7 суток.
    "telemetry-wincc-incremental": {
        "task": "telemetry.wincc.incremental_load",
        "schedule": crontab(minute=20),
    },
    # Техрежимы (план Qн/Qж) из ABAI — раз в сутки после синхронизации
    # оргструктуры и ремонтов. Режимы месячные; сводка допускает лаг до 31 дня.
    "telemetry-tech-regime-incremental": {
        "task": "telemetry.tech_regime.incremental_load",
        "schedule": crontab(hour=5, minute=45),
    },
    # Страховка детекторов: догнать станции, чьи курсоры отстали от данных
    # (потерянные задачи, первичный прогон истории, включённые правила).
    "detectors-sweep": {
        "task": "detectors.sweep",
        "schedule": crontab(minute="*/15"),
    },
    # R9 — правило суточное: считает вчерашние закрытые сутки. Гарантированный
    # прогон раз в день; диспетчер по приходу данных его продублирует не чаще
    # min_interval_sec, а повторный прогон идемпотентен.
    "detectors-load-imbalance-daily": {
        "task": "detectors.load_imbalance.run_incidents",
        "schedule": crontab(hour=4, minute=10),
    },
    # Суточные ведомости R2/R9 по подключённым НГДУ за вчера: после суточного
    # прогона R9 и ночных загрузок ABAI. Уже собранные даты пропускаются;
    # запрос за произвольную дату идёт через API тем же сборщиком.
    "detectors-daily-sheets": {
        "task": "detectors.daily_sheet.build",
        "schedule": crontab(hour=7, minute=30),
    },
    # Ежедневная догрузка привязок скважин к оргструктуре из ABAI (новые id +
    # правки dend/org). После org.sync, чтобы матрица по НГДУ опиралась на
    # свежую оргструктуру; её читают /wells/matrix, /wells/coords и разрешение
    # НГДУ по скважине в fill_repair_analytics.
    "wells-well-org-incremental": {
        "task": "wells.well_org.incremental_load",
        "schedule": crontab(hour=5, minute=15),
    },
    # Ежедневная догрузка периодов эксплуатации из ABAI (новые id + правки dend).
    "wells-well-expl-incremental": {
        "task": "wells.well_expl.incremental_load",
        "schedule": crontab(hour=5, minute=30),
    },
    # Ежедневная догрузка ГДИС из ABAI: метрики -> исследования -> значения,
    # плюс перечитывание исследований за 90 дней (заключения дописывают позже).
    "wells-gdis-incremental": {
        "task": "wells.gdis.incremental_load",
        "schedule": crontab(hour=5, minute=40),
    },
    # Ежедневная синхронизация оргструктуры из ABAI: типы организаций ->
    # организации -> бригады -> дедупликация бригад (порядок внутри таска).
    "org-sync-daily": {
        "task": "org.sync",
        "schedule": crontab(hour=4, minute=40),
    },
    # Ежедневная синхронизация ремонтов из ABAI: виды работ -> ремонты ->
    # актуализация незавершённых (порядок внутри таска). После org.sync,
    # чтобы ремонты ссылались на свежую оргструктуру.
    "repairs-sync-daily": {
        "task": "repairs.sync",
        "schedule": crontab(hour=5, minute=0),
    },
}
