"""Диспетчер детекторов: веер от «данные приехали» к задачам правил.

Загрузчики телеметрии зовут ``dispatch_detectors.delay(source, entity_ids)``
после коммита новых строк — и больше ничего о детекторах не знают. Диспетчер
читает реестр (enabled, source, дебаунс) и ставит задачу каждому подходящему
правилу. Подметальщик страхует от потерянных задач: раз в N минут сравнивает
курсоры с фронтом данных и допинывает отставших (он же выполняет первичный
прогон истории — у нового правила курсоров нет, значит отстают все станции).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from apps.celery_app import celery_app, run_async
from apps.detectors.conclusion.catalog import CONCLUSION_DETECTOR_CODES
from apps.detectors.conclusion.notify import CONCLUSION_TASK
from apps.detectors.load_imbalance.tasks.run_incidents.run_incidents import (
    DETECTOR_CODE as LOAD_IMBALANCE_CODE,
)
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorCursorRepository,
    DetectorRepository,
)
from apps.detectors.rod_breaks.tasks.run_incidents.run_incidents import (
    DETECTOR_CODE as ROD_BREAK_CODE,
)
from apps.detectors.rod_breaks.tasks.run_incidents.run_incidents import (
    target_stations,
)
from apps.models_registry import *  # noqa: F403
from apps.telemetry.models.sdmo import SdmoFcData
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

# Правило -> celery-задача его раннера. Новый детектор = новая пара здесь.
RUNNER_TASKS: dict[str, str] = {
    ROD_BREAK_CODE: "detectors.rod_breaks.run_incidents",
    LOAD_IMBALANCE_CODE: "detectors.load_imbalance.run_incidents",
}

# Подметальщик считает станцию отставшей, если курсор позади фронта данных
# больше, чем на это время (2 корзины по 15 минут).
SWEEP_LAG = timedelta(minutes=30)


async def _dispatch(source: str, entity_ids: list[int] | None) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_makers["app"]() as session:
        detectors = await DetectorRepository(session).list_enabled_by_source(
            source,
        )
        cursor_repo = DetectorCursorRepository(session)

        for detector in detectors:
            task = RUNNER_TASKS.get(detector.code)
            if task is None:
                logger.warning(
                    "Detector %s has no runner task registered",
                    detector.code,
                )
                continue

            if detector.min_interval_sec > 0:
                cursors = await cursor_repo.get_map(detector.code, entity_ids)
                last_run = max(
                    (c.last_run_at for c in cursors.values()),
                    default=None,
                )
                if last_run is not None and (now - last_run) < timedelta(
                    seconds=detector.min_interval_sec,
                ):
                    continue

            celery_app.send_task(task, kwargs={"entity_ids": entity_ids})


@celery_app.task(name="detectors.dispatch")
def dispatch_detectors(source: str, entity_ids: list[int] | None = None) -> None:
    run_async(_dispatch(source, entity_ids))


async def _sweep() -> None:
    """Найти станции, где курсор R2 отстал от фронта данных, и допнуть раннер."""
    async with session_makers["app"]() as session:
        detector = await DetectorRepository(session).get_by_code(ROD_BREAK_CODE)
        if detector is None or not detector.enabled:
            return

        stations = await target_stations(session)
        if not stations:
            return
        station_ids = [s.id for s in stations]

        front_rows = await session.execute(
            select(
                SdmoFcData.station_id,
                func.max(SdmoFcData.savetime),
            )
            .where(SdmoFcData.station_id.in_(station_ids))
            .group_by(SdmoFcData.station_id),
        )
        front = dict(front_rows.all())
        cursors = await DetectorCursorRepository(session).get_map(
            ROD_BREAK_CODE,
            station_ids,
        )

    lagging = []
    for station_id in station_ids:
        data_front = front.get(station_id)
        if data_front is None:
            continue  # данных по станции нет вовсе — догонять нечего
        cursor = cursors.get(station_id)
        if cursor is None or data_front - cursor.last_event_at > SWEEP_LAG:
            lagging.append(station_id)

    if lagging:
        logger.info("Detectors sweep: %s lagging stations", len(lagging))
        celery_app.send_task(
            RUNNER_TASKS[ROD_BREAK_CODE],
            kwargs={"entity_ids": lagging},
        )


async def _sweep_conclusions() -> None:
    """Достроить ИИ-заключения: активные эпизоды без completed под их уровень.

    Ловит потерянные задачи генерации, упавшие LLM-попытки (failed) и эпизоды,
    открытые до внедрения заключений.
    """
    async with session_makers["app"]() as session:
        missing = await DetectorConclusionRepository(
            session,
        ).list_active_incidents_missing_conclusion(CONCLUSION_DETECTOR_CODES)

    if not missing:
        return
    logger.info(
        "Detectors sweep: %s incidents without conclusion",
        len(missing),
    )
    for incident in missing:
        celery_app.send_task(
            CONCLUSION_TASK,
            kwargs={"incident_id": incident.id},
        )


@celery_app.task(name="detectors.sweep")
def sweep_detectors() -> None:
    run_async(_sweep())
    run_async(_sweep_conclusions())
