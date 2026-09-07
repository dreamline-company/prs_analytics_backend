import asyncio
from typing import TYPE_CHECKING

from apps.repairs.dto.internal.repositories.repair import (
    CreateRepairTypeDTO,
    UpdateRepairTypeDTO,
)
from apps.repairs.repositories.repair import RepairTypeRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories import ABAIRepairWorkTypeRepository

if TYPE_CHECKING:
    from shared.integrations.abai.models import RepairWorkType

logger = get_logger(__name__)


class LoadRepairWorkTypes:
    async def run(self) -> None:

        async with session_makers["abai"]() as abai_session:
            abai_repair_types_repo = ABAIRepairWorkTypeRepository(abai_session)
            abai_repair_types = await abai_repair_types_repo.get_list()

        async with session_makers["app"]() as app_session:
            app_repair_repository = RepairTypeRepository(app_session)
            app_repair_types = await app_repair_repository.get_list()

        abai_repair_type_ids = [r.id for r in abai_repair_types]
        app_repair_type_abai_ids = [r.abai_id for r in app_repair_types]

        new_repairs: list[RepairWorkType] = []
        update_repairs: list[RepairWorkType] = []
        for i, r_id in enumerate(abai_repair_type_ids):
            if r_id not in app_repair_type_abai_ids:
                new_repairs.append(abai_repair_types[i])

            else:
                app_repair_idx = app_repair_type_abai_ids.index(r_id)
                app_repair = app_repair_types[app_repair_idx]
                abai_repair = abai_repair_types[i]
                if (app_repair.name_ru != abai_repair.name_ru) or (
                    app_repair.name_ru_short != abai_repair.name_short_ru
                ):
                    update_repairs.append(abai_repair)

        if new_repairs or update_repairs:
            update_repairs_errors = []
            async with session_makers["app"]() as app_session:
                app_repair_repository = RepairTypeRepository(app_session)

                if new_repairs:
                    logger.info("Creating %s new repare types...", len(new_repairs))
                    batch_data = [
                        CreateRepairTypeDTO(
                            abai_id=r.id,
                            name_ru=r.name_ru,
                            name_ru_short=r.name_short_ru,
                        )
                        for r in new_repairs
                    ]
                    await app_repair_repository.bulk_create(data=batch_data)
                    await app_session.commit()

                if update_repairs:
                    logger.info("Updating %s repare types...", len(update_repairs))

                    for r in update_repairs:
                        try:
                            await app_repair_repository.update_by_abai_id(
                                abai_id=r.id,
                                data=UpdateRepairTypeDTO(
                                    name_ru=r.name_ru,
                                    name_ru_short=r.name_short_ru,
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Error updating repare type. ABAI_ID: %s; ",
                                r.id,
                            )
                            continue
                    await app_session.commit()

            logger.info("Repair work types are synced successfully.")
            logger.info(
                "Updated: %s. Errors: %s",
                len(update_repairs),
                len(update_repairs_errors),
            )
            logger.info("Created: %s", len(new_repairs))


async def main() -> None:
    await LoadRepairWorkTypes().run()


if __name__ == "__main__":
    asyncio.run(main())
