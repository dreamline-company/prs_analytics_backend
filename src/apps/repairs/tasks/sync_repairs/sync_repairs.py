"""Последовательная синхронизация ремонтов из ABAI.

Порядок фиксированный: справочник видов работ -> ремонты -> актуализация
незавершённых. Шаги зависят друг от друга, поэтому один celery-таск с
последовательными await вместо chain: упавший шаг прерывает прогон, следующие
шаги по неполным данным не выполняются.
"""

import asyncio

from apps.celery_app import celery_app, run_async
from apps.repairs.tasks.load_repairs.load_repair_work_type import (
    main as load_repair_work_types,
)
from apps.repairs.tasks.load_repairs.load_repairs import main as load_repairs
from apps.repairs.tasks.load_repairs.update_not_finished_repairs import (
    main as update_not_finished_repairs,
)
from core import get_logger

logger = get_logger(__name__)


async def main() -> None:
    logger.info("Repairs sync 1/3: load_repair_work_types")
    await load_repair_work_types()
    logger.info("Repairs sync 2/3: load_repairs")
    await load_repairs()
    logger.info("Repairs sync 3/3: update_not_finished_repairs")
    await update_not_finished_repairs()
    logger.info("Repairs sync done")


@celery_app.task(name="repairs.sync")
def sync_repairs() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
