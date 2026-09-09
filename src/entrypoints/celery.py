from celery.schedules import crontab

from apps.celery_app import celery_app

# Импорт регистрирует celery-задачи детекторов (диспетчер, подметальщик, R2, R9).
from apps.detectors.load_imbalance.tasks.run_incidents.run_incidents import (  # noqa: F401
    run_load_imbalance_incidents,
)
from apps.detectors.rod_breaks.tasks.run_incidents.run_incidents import (  # noqa: F401
    run_rod_break_incidents,
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

# Импорт регистрирует добытчики источников ремонтов (ABAI, КБРС, УТО) и
# аналитику ремонтов (по событию и страховочный проход).
from apps.repairs.tasks.fetch_sources.fetch_docs import fetch_repair_docs  # noqa: F401
from apps.repairs.tasks.fetch_sources.fetch_dynamograms import (  # noqa: F401
    fetch_repair_dynamograms,
)
from apps.repairs.tasks.fetch_sources.fetch_spo_toucan import (  # noqa: F401
    fetch_repair_spo_toucan,
)
from apps.repairs.tasks.fetch_sources.fetch_transport import (  # noqa: F401
    fetch_repair_transport,
)
from apps.repairs.tasks.fetch_sources.link_spo import link_repair_spo  # noqa: F401
from apps.repairs.tasks.fill_analytics.fill_repair_analytics import (  # noqa: F401
    run_repair_analytics,
    sweep_repair_analytics,
)

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
    # Аналитика ремонтов. Источники тянут отдельные таски, аналитика идёт по
    # событию от них (repairs.analytics.run_repair с debounce), а кроны ниже —
    # страховка: догоняют потерянные события и первичный проход по кандидатам.
    # ABAI не пушит изменения — только опрос: два запроса на ремонт, поэтому
    # каждые 15 минут. СПО в основном приходит событием из опросчика КБРС
    # (repairs.link_spo по measure_id); прямой Toucan — редкий запасной путь
    # для ремонтов без замеров в kbrs_measure.
    "repairs-fetch-docs": {
        "task": "repairs.fetch.docs",
        "schedule": crontab(minute="*/15"),
    },
    "repairs-fetch-dynamograms": {
        "task": "repairs.fetch.dynamograms",
        "schedule": crontab(minute="5-59/15"),
    },
    "repairs-link-spo-sweep": {
        "task": "repairs.link_spo",
        "schedule": crontab(minute="*/30"),
    },
    "repairs-fetch-spo-toucan": {
        "task": "repairs.fetch.spo_toucan",
        "schedule": crontab(minute=40, hour="*/6"),
    },
    "repairs-fetch-transport": {
        "task": "repairs.fetch.transport",
        "schedule": crontab(minute=50, hour="*/2"),
    },
    "repairs-analytics-sweep": {
        "task": "repairs.analytics.sweep",
        "schedule": crontab(minute=30),
    },
}
