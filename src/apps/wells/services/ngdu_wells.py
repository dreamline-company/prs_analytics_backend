"""Скважины, которые сейчас числятся за НГДУ.

Цепочка разрешения (общая для матрицы скважин и её производных):
  1. Взять ``Org`` НГДУ по локальному id → собрать все дочерние орг. объекты
     по ``parent_id`` (он хранит ABAI id).
  2. Спросить ABAI ``well_org`` по этому поддереву → сгруппировать по скважине
     и выбрать текущую привязку (последняя по ``dbeg``, tiebreak по ``id``).
  3. Оставить скважины, чья текущая привязка всё ещё внутри поддерева НГДУ
     (то есть скважину не перевели в другое подразделение).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.org.models.org import Org
    from apps.org.repositories.org import OrgRepository
    from apps.wells.models.well import Well
    from apps.wells.repositories.well import WellRepository
    from shared.integrations.abai.models import WellOrg
    from shared.integrations.abai.repositories.well_orgs import ABAIWellOrgRepository


class NGDUWellsService:
    def __init__(
        self,
        *,
        org_repository: OrgRepository,
        well_repository: WellRepository,
        abai_well_org_repository: ABAIWellOrgRepository,
    ) -> None:
        self.org_repository = org_repository
        self.well_repository = well_repository
        self.abai_well_org_repository = abai_well_org_repository

    async def list_wells(self, ngdu_id: int) -> list[Well]:
        """Неудалённые скважины НГДУ; пустой список, если НГДУ нет или он пуст."""
        ngdu = await self.org_repository.get_by_id(ngdu_id)
        if ngdu is None:
            return []

        descendants_abai_ids = await self._descendants_abai_ids(ngdu)
        if not descendants_abai_ids:
            return []

        well_orgs = await self.abai_well_org_repository.list_by_orgs(
            list(descendants_abai_ids),
        )
        if not well_orgs:
            return []

        current_by_well: dict[int, WellOrg] = {}
        for row in well_orgs:
            if row.well is None or row.org is None:
                continue
            existing = current_by_well.get(row.well)
            if existing is None or self._is_newer(row, existing):
                current_by_well[row.well] = row

        well_abai_ids = [
            well_abai_id
            for well_abai_id, current in current_by_well.items()
            if current.org in descendants_abai_ids
        ]
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

    @staticmethod
    def _is_newer(candidate: WellOrg, current: WellOrg) -> bool:
        c_dbeg = candidate.dbeg or date.min
        cur_dbeg = current.dbeg or date.min
        if c_dbeg != cur_dbeg:
            return c_dbeg > cur_dbeg
        return candidate.id > current.id
