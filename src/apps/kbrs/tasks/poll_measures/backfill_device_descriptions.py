"""Разовое дозаполнение ``kbrs_measure.device_description`` из справочника Toucan.

    cd src && python -m apps.kbrs.tasks.poll_measures.backfill_device_descriptions

Опросчик пишет описание прибора у новых и обновлённых замеров; история,
снятая до появления поля, заполняется этой командой по паре
(owner_id, device_id). Справочник приборов приходит с логином — RPC на прибор
не нужен, один проход по пулу на НГДУ.
"""

import asyncio
from collections import defaultdict

from apps.kbrs.repositories.measure import KbrsMeasureRepository
from apps.kbrs.tasks.poll_measures.poller import device_description_map
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.integrations.kbrs.api import (
    ToucanClientConfig,
    ToucanClientPool,
    ToucanCredentialsDto,
)

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    pool = await ToucanClientPool.create(
        size=1,
        config=ToucanClientConfig(host=settings.KBRS_HOST),
        credentials=ToucanCredentialsDto(
            login=settings.KBRS_LOGIN,
            password=settings.KBRS_PASSWORD,
        ),
    )
    updated = 0
    missing = 0
    try:
        async with session_makers["app"]() as session:
            repo = KbrsMeasureRepository(session)
            pairs = await repo.list_devices_without_description()
            devices_by_owner: dict[int, list[int]] = defaultdict(list)
            for owner_id, device_id in pairs:
                devices_by_owner[owner_id].append(device_id)
            logger.info(
                "Backfill: %s devices without description across %s owners.",
                len(pairs),
                len(devices_by_owner),
            )
            for owner_id, device_ids in sorted(devices_by_owner.items()):
                devices = await pool.call_with_retry(
                    lambda client, owner_id=owner_id: client.list_devices(
                        owner_id=owner_id,
                    ),
                )
                descriptions = device_description_map(devices)
                for device_id in device_ids:
                    description = descriptions.get((owner_id, device_id))
                    if description is None:
                        missing += 1
                        continue
                    updated += await repo.set_device_description(
                        owner_id=owner_id,
                        device_id=device_id,
                        description=description,
                    )
            await session.commit()
    finally:
        await pool.close()
    logger.info(
        "Backfill done: rows updated=%s, devices not in directory=%s.",
        updated,
        missing,
    )


if __name__ == "__main__":
    asyncio.run(main())
