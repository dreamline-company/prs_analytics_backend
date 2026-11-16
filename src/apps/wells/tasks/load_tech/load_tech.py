"""Синхронизация технологических объектов ABAI и привязок к ним скважин.

Источник — emg.tech_type, emg.tech, emg.tech_history, emg.well_tech: отсюда
видно, на какой ГЗУ и каком отводе стоит скважина. Таблицы маленькие (сотни
объектов, тысячи привязок), поэтому за прогон они читаются целиком и
сверяются с копией по ``abai_id`` в порядке тип → объект → имя → привязка;
как устроены сверка и защита от сломанного источника — см.
``apps.wells.services.abai_mirror_sync``.

Пропускаются имя объекта, которого нет в источнике, и привязка без скважины,
к скважине не из ``wells_well`` (FK на ``wells_well.abai_id``) или к объекту,
которого нет. Объект с типом, которого нет в справочнике, пишется без типа.
"""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.tech import (
    TechDTO,
    TechHistoryDTO,
    TechTypeDTO,
    WellTechDTO,
)
from apps.wells.repositories import (
    TechHistoryRepository,
    TechRepository,
    TechTypeRepository,
    WellRepository,
    WellTechRepository,
)
from apps.wells.services.abai_mirror_sync import (
    MirrorTable,
    TableStats,
    print_summary,
    sync_mirror,
)
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import Tech as ABAITech
from shared.integrations.abai.models import TechHistory as ABAITechHistory
from shared.integrations.abai.models import TechType as ABAITechType
from shared.integrations.abai.models import WellTech as ABAIWellTech
from shared.integrations.abai.repositories import (
    ABAITechHistoryRepository,
    ABAITechRepository,
    ABAITechTypeRepository,
    ABAIWellTechRepository,
)


def tech_type_dto(row: ABAITechType) -> TechTypeDTO:
    return TechTypeDTO(
        abai_id=row.id,
        name_ru=row.name_ru,
        name_short_ru=row.name_short_ru,
        code=row.code,
    )


def tech_dto(row: ABAITech, type_ids: set[int]) -> TechDTO:
    return TechDTO(
        abai_id=row.id,
        abai_tech_type_id=row.tech_type if row.tech_type in type_ids else None,
    )


def tech_history_dto(row: ABAITechHistory, tech_ids: set[int]) -> TechHistoryDTO | None:
    if row.tech not in tech_ids:
        return None
    return TechHistoryDTO(
        abai_id=row.id,
        abai_tech_id=row.tech,
        abai_parent_id=row.parent,
        name_ru=row.name_ru,
        name_short_ru=row.name_short_ru,
        dbeg=row.dbeg,
        dend=row.dend,
    )


def well_tech_dto(
    row: ABAIWellTech,
    tech_ids: set[int],
    well_ids: set[int],
) -> WellTechDTO | None:
    if row.tech not in tech_ids or row.well not in well_ids:
        return None
    return WellTechDTO(
        abai_id=row.id,
        abai_well_id=row.well,
        abai_tech_id=row.tech,
        tap=row.tap,
        dbeg=row.dbeg,
        dend=row.dend,
    )


class LoadTech:
    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.app_session = app_session
        self.abai_tech_type_repo = ABAITechTypeRepository(session=abai_session)
        self.abai_tech_repo = ABAITechRepository(session=abai_session)
        self.abai_tech_history_repo = ABAITechHistoryRepository(session=abai_session)
        self.abai_well_tech_repo = ABAIWellTechRepository(session=abai_session)
        self.tech_type_repo = TechTypeRepository(session=app_session)
        self.tech_repo = TechRepository(session=app_session)
        self.tech_history_repo = TechHistoryRepository(session=app_session)
        self.well_tech_repo = WellTechRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)

    async def run(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, TableStats]:
        types = [
            tech_type_dto(row) for row in await self.abai_tech_type_repo.list_all()
        ]
        type_ids = {dto.abai_id for dto in types}

        techs = [
            tech_dto(row, type_ids) for row in await self.abai_tech_repo.list_all()
        ]
        tech_ids = {dto.abai_id for dto in techs}

        history_rows = await self.abai_tech_history_repo.list_all()
        histories = [
            dto
            for row in history_rows
            if (dto := tech_history_dto(row, tech_ids)) is not None
        ]

        well_ids = await self.well_repo.list_abai_ids()
        link_rows = await self.abai_well_tech_repo.list_all()
        links = [
            dto
            for row in link_rows
            if (dto := well_tech_dto(row, tech_ids, well_ids)) is not None
        ]

        return await sync_mirror(
            self.app_session,
            (
                MirrorTable("tech_type", self.tech_type_repo, types, len(types)),
                MirrorTable("tech", self.tech_repo, techs, len(techs)),
                MirrorTable(
                    "tech_history",
                    self.tech_history_repo,
                    histories,
                    len(history_rows),
                ),
                MirrorTable("well_tech", self.well_tech_repo, links, len(link_rows)),
            ),
            label="Tech",
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
        return await LoadTech(
            abai_session=abai_session,
            app_session=app_session,
        ).run(dry_run=dry_run, force=force)


@celery_app.task(name="wells.tech.sync")
def sync_tech() -> None:
    run_async(main())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Технологические объекты и ГЗУ из ABAI",
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
