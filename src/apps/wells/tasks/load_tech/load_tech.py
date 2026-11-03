"""Синхронизация технологических объектов ABAI и привязок к ним скважин.

Источник — emg.tech_type, emg.tech, emg.tech_history, emg.well_tech: отсюда
видно, на какой ГЗУ и каком отводе стоит скважина. Таблицы маленькие (сотни
объектов, тысячи привязок), поэтому за прогон они читаются целиком и
сверяются с копией по ``abai_id``: новые строки добавляются, изменённые
переписываются, исчезнувшие удаляются. Так копия ловит и правки закрытых
периодов, и удаления в источнике.

Порядок записи — сначала добавления и правки от справочника к привязкам
(тип → объект → имя → привязка), затем удаления в обратном порядке, чтобы
внешние ключи не повисли ни на одном шаге.

Пропускаются имя объекта, которого нет в источнике, и привязка без скважины,
к скважине не из ``wells_well`` (FK на ``wells_well.abai_id``) или к объекту,
которого нет. Объект с типом, которого нет в справочнике, пишется без типа.

Если источник вдруг отдал меньше половины строк копии (пустая таблица после
перезаливки, пропавшие права), прогон прерывается целиком: иначе сверка
стёрла бы зеркало вместе со всеми зависимыми таблицами.
"""

import argparse
import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.tech import (
    TechDTO,
    TechHistoryDTO,
    TechTypeDTO,
    WellTechDTO,
)
from apps.wells.models.tech import Tech, TechHistory, TechType, WellTech
from apps.wells.repositories import (
    TechHistoryRepository,
    TechRepository,
    TechTypeRepository,
    WellRepository,
    WellTechRepository,
)
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.errors import AppError
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

logger = get_logger(__name__)


class SourceShrunkError(AppError):
    message = "ABAI returned far fewer rows than the mirror holds"
    code = "abai_tech_source_shrunk"


@dataclass(frozen=True, slots=True)
class SyncPlan[DTOT: (TechTypeDTO, TechDTO, TechHistoryDTO, WellTechDTO)]:
    """Что сделать с одной таблицей копии, чтобы она совпала с источником."""

    create: list[DTOT] = field(default_factory=list)
    update: list[DTOT] = field(default_factory=list)
    delete: list[int] = field(default_factory=list)


@dataclass(slots=True)
class TableStats:
    source: int = 0
    skipped: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0


def plan_sync[DTOT: (TechTypeDTO, TechDTO, TechHistoryDTO, WellTechDTO)](
    source: Iterable[DTOT],
    local: Iterable[TechType | Tech | TechHistory | WellTech],
) -> SyncPlan[DTOT]:
    """Сравнить строки источника с копией по ``abai_id``.

    Строка копии считается изменённой, если хоть одно поле DTO отличается от
    одноимённого атрибута модели.
    """
    local_by_id = {row.abai_id: row for row in local}
    plan: SyncPlan[DTOT] = SyncPlan()
    seen: set[int] = set()

    for dto in source:
        seen.add(dto.abai_id)
        current = local_by_id.get(dto.abai_id)
        if current is None:
            plan.create.append(dto)
        elif any(
            getattr(current, name) != value for name, value in dto.model_dump().items()
        ):
            plan.update.append(dto)

    plan.delete.extend(sorted(set(local_by_id) - seen))
    return plan


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
    # Источник короче этой доли копии — считаем его сломанным и не сверяем.
    MIN_SOURCE_SHARE = 0.5

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

    async def run(self, *, dry_run: bool = False) -> dict[str, TableStats]:
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

        # От справочника к привязкам: добавления и правки идут в этом порядке,
        # удаления — в обратном.
        tables = (
            ("tech_type", self.tech_type_repo, types, len(types)),
            ("tech", self.tech_repo, techs, len(techs)),
            ("tech_history", self.tech_history_repo, histories, len(history_rows)),
            ("well_tech", self.well_tech_repo, links, len(link_rows)),
        )
        mirrors = [await repo.list_all() for _, repo, _, _ in tables]
        for (name, _, dtos, _), mirror in zip(tables, mirrors, strict=True):
            self._check_source(name, source=len(dtos), mirror=len(mirror))
        plans = [
            plan_sync(dtos, mirror)
            for (_, _, dtos, _), mirror in zip(tables, mirrors, strict=True)
        ]

        stats: dict[str, TableStats] = {}
        try:
            for (name, repo, dtos, source_count), plan in zip(
                tables,
                plans,
                strict=True,
            ):
                stats[name] = TableStats(
                    source=source_count,
                    skipped=source_count - len(dtos),
                    created=len(plan.create),
                    updated=len(plan.update),
                    deleted=len(plan.delete),
                )
                await repo.bulk_create(plan.create)
                for dto in plan.update:
                    await repo.update_by_abai_id(dto)
            for (_, repo, _, _), plan in reversed(
                list(zip(tables, plans, strict=True)),
            ):
                await repo.delete_by_abai_ids(plan.delete)

            if dry_run:
                await self.app_session.rollback()
            else:
                await self.app_session.commit()
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while syncing ABAI tech objects")
            raise

        logger.info(
            "Tech sync finished%s: %s",
            " (dry run)" if dry_run else "",
            ", ".join(
                f"{name} +{s.created} ~{s.updated} -{s.deleted} skip {s.skipped}"
                for name, s in stats.items()
            ),
        )
        return stats

    def _check_source(self, name: str, *, source: int, mirror: int) -> None:
        if source < self.MIN_SOURCE_SHARE * mirror:
            logger.error(
                "Tech sync aborted: %s has %s rows in ABAI, %s in the mirror",
                name,
                source,
                mirror,
            )
            raise SourceShrunkError


async def main(*, dry_run: bool = False) -> dict[str, TableStats]:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        return await LoadTech(
            abai_session=abai_session,
            app_session=app_session,
        ).run(dry_run=dry_run)


@celery_app.task(name="wells.tech.sync")
def sync_tech() -> None:
    run_async(main())


def _print_summary(stats: dict[str, TableStats], *, dry_run: bool) -> None:
    print("DRY RUN — ничего не записано" if dry_run else "Записано")  # noqa: T201
    for name, s in stats.items():
        print(  # noqa: T201
            f"{name:13} источник {s.source:6}  пропущено {s.skipped:5}  "
            f"добавлено {s.created:6}  изменено {s.updated:5}  удалено {s.deleted:5}",
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Технологические объекты и ГЗУ из ABAI",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _print_summary(asyncio.run(main(dry_run=args.dry_run)), dry_run=args.dry_run)
