"""Разово: тип ремонта ABAI (КРС/ПРС/...) у уже загруженных ремонтов.

Новые ремонты получают ``abai_repair_type_id`` в ``LoadRepairs``; этот скрипт
заполняет те, что загружены до появления колонки. Повторный запуск трогает
только ремонты без типа.
"""

import asyncio
from collections import defaultdict

from sqlalchemy import select

from apps.models_registry import *  # noqa
from apps.repairs.repositories.repair import RepairRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import WellWorkover

logger = get_logger(__name__)

CHUNK_SIZE = 5000


async def main() -> None:
    async with (
        session_makers["app"]() as app_session,
        session_makers["abai"]() as abai_session,
    ):
        repo = RepairRepository(app_session)
        abai_ids = await repo.list_abai_ids_without_abai_repair_type()
        filled = 0
        for start in range(0, len(abai_ids), CHUNK_SIZE):
            chunk = abai_ids[start : start + CHUNK_SIZE]
            rows = await abai_session.execute(
                select(WellWorkover.id, WellWorkover.repair_type).where(
                    WellWorkover.id.in_(chunk),
                ),
            )
            ids_by_type: dict[int, list[int]] = defaultdict(list)
            for abai_id, repair_type in rows.tuples():
                if repair_type is not None:
                    ids_by_type[repair_type].append(abai_id)
            for repair_type, ids in ids_by_type.items():
                await repo.set_abai_repair_type(ids, repair_type)
                filled += len(ids)
            await app_session.commit()
        logger.info(
            "ABAI repair type backfilled: %s of %s repairs without type.",
            filled,
            len(abai_ids),
        )


if __name__ == "__main__":
    asyncio.run(main())
