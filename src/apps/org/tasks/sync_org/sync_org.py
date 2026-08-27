"""Последовательная синхронизация оргструктуры из ABAI.

Порядок фиксированный: типы организаций -> организации -> бригады ->
дедупликация бригад. Каждый следующий шаг опирается на данные предыдущего,
поэтому один celery-таск с последовательными await вместо chain: упавший шаг
прерывает прогон, следующие шаги по неполным данным не выполняются.
"""

import asyncio

from apps.celery_app import celery_app, run_async
from apps.org.tasks.fill_unique_brigades.fill_unique_brigades import (
    main as fill_unique_brigades,
)
from apps.org.tasks.load_brigades.load_brigades import main as load_brigades
from apps.org.tasks.load_orgs.load_org_types import main as load_org_types
from apps.org.tasks.load_orgs.load_orgs import main as load_orgs
from core import get_logger

logger = get_logger(__name__)


async def main() -> None:
    logger.info("Org sync 1/4: load_org_types")
    await load_org_types()
    logger.info("Org sync 2/4: load_orgs")
    await load_orgs()
    logger.info("Org sync 3/4: load_brigades")
    await load_brigades()
    logger.info("Org sync 4/4: fill_unique_brigades")
    await fill_unique_brigades()
    logger.info("Org sync done")


@celery_app.task(name="org.sync")
def sync_org() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
