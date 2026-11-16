"""Синхронизация категорий и горизонтов скважин из ABAI.

Источник — emg.well_category_type, emg.well_category, emg.geo_type, emg.geo,
emg.well_geo: категория скважины (нефтяная, нагнетательная, наблюдательная…)
и геообъекты, на которые она заведена, — почти всегда горизонты. Таблицы
маленькие (десятки тысяч строк), поэтому за прогон они читаются целиком и
сверяются с копией по ``abai_id``; как устроены сверка и защита от сломанного
источника — см. ``apps.wells.services.abai_mirror_sync``.

Две независимые цепочки — тип категории → категория и тип геообъекта →
геообъект → привязка — пишутся в одной транзакции.

Пропускаются категория и привязка к скважине не из ``wells_well`` (FK на
``wells_well.abai_id``), категория неизвестного типа и привязка к геообъекту,
которого нет в источнике. Геообъект с неизвестным типом пишется без типа.
Даты привязок раньше 1900 года (в источнике есть даты до нашей эры и
1899-12-31) приходят из ABAI уже как NULL — см.
``shared.integrations.abai.models.WellGeo``.
"""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.category_geo import (
    GeoDTO,
    GeoTypeDTO,
    WellCategoryDTO,
    WellCategoryTypeDTO,
    WellGeoDTO,
)
from apps.wells.repositories import (
    GeoRepository,
    GeoTypeRepository,
    WellCategoryRepository,
    WellCategoryTypeRepository,
    WellGeoRepository,
    WellRepository,
)
from apps.wells.services.abai_mirror_sync import (
    MirrorTable,
    TableStats,
    print_summary,
    sync_mirror,
)
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import Geo as ABAIGeo
from shared.integrations.abai.models import GeoType as ABAIGeoType
from shared.integrations.abai.models import WellCategory as ABAIWellCategory
from shared.integrations.abai.models import WellCategoryType as ABAIWellCategoryType
from shared.integrations.abai.models import WellGeo as ABAIWellGeo
from shared.integrations.abai.repositories import (
    ABAIGeoRepository,
    ABAIGeoTypeRepository,
    ABAIWellCategoryRepository,
    ABAIWellCategoryTypeRepository,
    ABAIWellGeoRepository,
)


def category_type_dto(row: ABAIWellCategoryType) -> WellCategoryTypeDTO:
    return WellCategoryTypeDTO(
        abai_id=row.id,
        name_ru=row.name_ru,
        name_short_ru=row.name_short_ru,
        code=row.code,
    )


def category_dto(
    row: ABAIWellCategory,
    type_ids: set[int],
    well_ids: set[int],
) -> WellCategoryDTO | None:
    if row.category not in type_ids or row.well not in well_ids:
        return None
    return WellCategoryDTO(
        abai_id=row.id,
        abai_well_id=row.well,
        abai_category_type_id=row.category,
        dbeg=row.dbeg,
        dend=row.dend,
    )


def geo_type_dto(row: ABAIGeoType) -> GeoTypeDTO:
    return GeoTypeDTO(
        abai_id=row.id,
        name_ru=row.name_ru,
        name_short_ru=row.name_short_ru,
        code=row.code,
    )


def geo_dto(row: ABAIGeo, type_ids: set[int]) -> GeoDTO:
    return GeoDTO(
        abai_id=row.id,
        abai_geo_type_id=row.geo_type if row.geo_type in type_ids else None,
        name_ru=row.name_ru,
        name_short_ru=row.name_short_ru,
        field_code=row.field_code,
    )


def well_geo_dto(
    row: ABAIWellGeo,
    geo_ids: set[int],
    well_ids: set[int],
) -> WellGeoDTO | None:
    if row.geo not in geo_ids or row.well not in well_ids:
        return None
    return WellGeoDTO(
        abai_id=row.id,
        abai_well_id=row.well,
        abai_geo_id=row.geo,
        dbeg=row.dbeg,
        dend=row.dend,
    )


class LoadCategoryGeo:
    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.app_session = app_session
        self.abai_category_type_repo = ABAIWellCategoryTypeRepository(
            session=abai_session,
        )
        self.abai_category_repo = ABAIWellCategoryRepository(session=abai_session)
        self.abai_geo_type_repo = ABAIGeoTypeRepository(session=abai_session)
        self.abai_geo_repo = ABAIGeoRepository(session=abai_session)
        self.abai_well_geo_repo = ABAIWellGeoRepository(session=abai_session)
        self.category_type_repo = WellCategoryTypeRepository(session=app_session)
        self.category_repo = WellCategoryRepository(session=app_session)
        self.geo_type_repo = GeoTypeRepository(session=app_session)
        self.geo_repo = GeoRepository(session=app_session)
        self.well_geo_repo = WellGeoRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)

    async def run(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, TableStats]:
        well_ids = await self.well_repo.list_abai_ids()

        category_types = [
            category_type_dto(row)
            for row in await self.abai_category_type_repo.list_all()
        ]
        category_type_ids = {dto.abai_id for dto in category_types}
        category_rows = await self.abai_category_repo.list_all()
        categories = [
            dto
            for row in category_rows
            if (dto := category_dto(row, category_type_ids, well_ids)) is not None
        ]

        geo_types = [
            geo_type_dto(row) for row in await self.abai_geo_type_repo.list_all()
        ]
        geo_type_ids = {dto.abai_id for dto in geo_types}
        geos = [
            geo_dto(row, geo_type_ids) for row in await self.abai_geo_repo.list_all()
        ]
        geo_ids = {dto.abai_id for dto in geos}
        link_rows = await self.abai_well_geo_repo.list_all()
        links = [
            dto
            for row in link_rows
            if (dto := well_geo_dto(row, geo_ids, well_ids)) is not None
        ]

        # Справочник раньше привязок: добавления и правки идут в этом порядке,
        # удаления — в обратном.
        return await sync_mirror(
            self.app_session,
            (
                MirrorTable(
                    "category_type",
                    self.category_type_repo,
                    category_types,
                    len(category_types),
                ),
                MirrorTable(
                    "category",
                    self.category_repo,
                    categories,
                    len(category_rows),
                ),
                MirrorTable("geo_type", self.geo_type_repo, geo_types, len(geo_types)),
                MirrorTable("geo", self.geo_repo, geos, len(geos)),
                MirrorTable("well_geo", self.well_geo_repo, links, len(link_rows)),
            ),
            label="Category/geo",
            dry_run=dry_run,
            force=force,
        )


async def main(
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, TableStats]:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        return await LoadCategoryGeo(
            abai_session=abai_session,
            app_session=app_session,
        ).run(dry_run=dry_run, force=force)


@celery_app.task(name="wells.category_geo.sync")
def sync_category_geo() -> None:
    run_async(main())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Категории и горизонты скважин из ABAI",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="записать, даже если удалений больше порога",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    print_summary(
        asyncio.run(main(dry_run=args.dry_run, force=args.force)),
        dry_run=args.dry_run,
    )
