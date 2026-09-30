"""Перепривязка ремонтов к бригаде своего НГДУ.

Бригады в org_unique_brigade долго были только у Кайнармунайгаза (ошибка
сопоставления органа в fill_unique_brigades), а сводки ищут бригаду по имени —
ремонты Жайыка, Жылыоя и Доссора привязались к одноимённым бригадам Кайнара.
Здесь каждая связка, чья бригада не из НГДУ скважины ремонта, переводится на
бригаду с тем же названием в НГДУ скважины. Запускать после
fill_unique_brigades; повторный запуск ничего не меняет.

    python -m apps.repairs.tasks.relink_repair_brigades.relink_repair_brigades
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select

from apps.models_registry import *  # noqa: F403
from apps.org.models.brigade import UniqueBrigade
from apps.org.repositories import OrgRepository, UniqueBrigadeRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.repairs.dto.internal.repositories.brigade import UpdateRepairBrigadeDTO
from apps.repairs.models.brigade import RepairBrigade
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.wells.repositories.well_org import WellOrgRepository
from core import get_logger
from shared.database.sql.setup import session_makers

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from apps.org.models.org import Org

logger = get_logger(__name__)


async def relink(session: AsyncSession) -> tuple[int, int]:
    """(перепривязано, не нашлось бригады в НГДУ скважины)."""
    rows = (
        await session.execute(
            select(RepairBrigade.id, Repair.abai_well_id, UniqueBrigade)
            .join(Repair, Repair.id == RepairBrigade.repair_id)
            .join(UniqueBrigade, UniqueBrigade.id == RepairBrigade.brigade_id),
        )
    ).all()
    brigades = {
        (brigade.name, brigade.ngdu_id): brigade
        for brigade in await UniqueBrigadeRepository(session).list_all()
    }
    get_ngdu = GetNGDUForWellUseCase(
        well_org_repository=WellOrgRepository(session),
        org_repository=OrgRepository(session),
    )
    link_repo = RepairBrigadeRepository(session)
    ngdu_cache: dict[int, Org | None] = {}
    moved = missing = 0
    for link_id, abai_well_id, brigade in rows:
        if abai_well_id not in ngdu_cache:
            ngdu_cache[abai_well_id] = await get_ngdu.execute(abai_well_id)
        ngdu = ngdu_cache[abai_well_id]
        if ngdu is None or ngdu.id == brigade.ngdu_id:
            continue
        target = brigades.get((brigade.name, ngdu.id))
        if target is None:
            logger.warning(
                "Link id=%s: no %r in ngdu_id=%s — kept.",
                link_id,
                brigade.name,
                ngdu.id,
            )
            missing += 1
            continue
        await link_repo.update_by_id(
            link_id,
            UpdateRepairBrigadeDTO(brigade_id=target.id),
        )
        moved += 1
    return moved, missing


async def main() -> None:
    async with session_makers["app"]() as session:
        moved, missing = await relink(session)
        await session.commit()
    logger.info("Repair brigades relinked: moved=%s, missing=%s", moved, missing)


if __name__ == "__main__":
    asyncio.run(main())
