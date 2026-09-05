"""Скважины, которые сейчас числятся за НГДУ.

Цепочка разрешения (общая для матрицы скважин и её производных):
  1. Взять ``Org`` НГДУ по локальному id → собрать все дочерние орг. объекты
     по ``parent_id`` (он хранит ABAI id).
  2. По локальному зеркалу ``wells_well_org`` найти скважины, чья текущая
     привязка (последняя по ``dbeg``, tiebreak по ``abai_id``) лежит внутри
     поддерева НГДУ. Скважины, переведённые в другое подразделение, отсеиваются
     на стороне БД.
  3. Отдать неудалённые скважины из ``wells_well``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.org.models.org import Org
    from apps.org.repositories.org import OrgRepository
    from apps.wells.models.well import Well
    from apps.wells.repositories.well import WellRepository
    from apps.wells.repositories.well_org import WellOrgRepository


class NGDUWellsService:
    def __init__(
        self,
        *,
        org_repository: OrgRepository,
        well_repository: WellRepository,
        well_org_repository: WellOrgRepository,
    ) -> None:
        self.org_repository = org_repository
        self.well_repository = well_repository
        self.well_org_repository = well_org_repository

    async def list_wells(self, ngdu_id: int) -> list[Well]:
        """Неудалённые скважины НГДУ; пустой список, если НГДУ нет или он пуст."""
        ngdu = await self.org_repository.get_by_id(ngdu_id)
        if ngdu is None:
            return []

        descendants_abai_ids = await self._descendants_abai_ids(ngdu)
        if not descendants_abai_ids:
            return []

        well_abai_ids = (
            await self.well_org_repository.list_current_abai_well_ids_by_abai_org_ids(
                sorted(descendants_abai_ids),
            )
        )
        if not well_abai_ids:
            return []

        wells = await self.well_repository.list_by_abai_ids(well_abai_ids)
        return [well for well in wells if not well.is_deleted]

    async def _descendants_abai_ids(self, ngdu: Org) -> set[int]:
        all_orgs = await self.org_repository.list_all()
        children_by_parent: dict[int, list[Org]] = defaultdict(list)
        for org in all_orgs:
            if org.parent_id is not None:
                children_by_parent[org.parent_id].append(org)

        result: set[int] = {ngdu.abai_id}
        stack: list[int] = [ngdu.abai_id]
        while stack:
            parent_abai_id = stack.pop()
            for child in children_by_parent.get(parent_abai_id, ()):
                if child.abai_id in result:
                    continue
                result.add(child.abai_id)
                stack.append(child.abai_id)
        return result
