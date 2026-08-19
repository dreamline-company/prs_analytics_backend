"""Разовая загрузка справочника способов эксплуатации (emg.well_expl_type).

Справочник маленький (десятки строк) и меняется редко, поэтому тянем таблицу
целиком. Таска идемпотентна: новые abai_id создаются, изменившиеся строки
обновляются, лишнего не удаляем (на wells_well_expl_type ссылается well_expl).
"""

import asyncio

from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.well_expl import (
    CreateWellExplTypeDTO,
    UpdateWellExplTypeDTO,
)
from apps.wells.models.well_expl import WellExplType
from apps.wells.repositories import WellExplTypeRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import WellExplType as ABAIWellExplType
from shared.integrations.abai.repositories import ABAIWellExplTypeRepository

logger = get_logger(__name__)


class LoadWellExplTypes:
    async def run(self) -> None:
        async with session_makers["abai"]() as abai_session:
            abai_types = await ABAIWellExplTypeRepository(abai_session).list_all()

        async with session_makers["app"]() as app_session:
            repo = WellExplTypeRepository(app_session)
            app_types_by_abai_id = {t.abai_id: t for t in await repo.list_all()}

            new_types = [t for t in abai_types if t.id not in app_types_by_abai_id]
            changed_types = [
                t
                for t in abai_types
                if t.id in app_types_by_abai_id
                and self._is_changed(app_types_by_abai_id[t.id], t)
            ]

            if new_types:
                await repo.bulk_create(
                    [
                        CreateWellExplTypeDTO(
                            abai_id=t.id,
                            name_ru=t.name_ru,
                            name_short_ru=t.name_short_ru,
                            tbd_id=t.tbd_id,
                            code=t.code,
                        )
                        for t in new_types
                    ],
                )

            for t in changed_types:
                await repo.update_by_abai_id(
                    abai_id=t.id,
                    data=UpdateWellExplTypeDTO(
                        name_ru=t.name_ru,
                        name_short_ru=t.name_short_ru,
                        tbd_id=t.tbd_id,
                        code=t.code,
                    ),
                )

            await app_session.commit()

        logger.info(
            "Well expl types sync finished: source=%s, created=%s, updated=%s",
            len(abai_types),
            len(new_types),
            len(changed_types),
        )

    @staticmethod
    def _is_changed(app_type: WellExplType, abai_type: ABAIWellExplType) -> bool:
        return (
            app_type.name_ru != abai_type.name_ru
            or app_type.name_short_ru != abai_type.name_short_ru
            or app_type.tbd_id != abai_type.tbd_id
            or app_type.code != abai_type.code
        )


async def main() -> None:
    await LoadWellExplTypes().run()


if __name__ == "__main__":
    asyncio.run(main())
