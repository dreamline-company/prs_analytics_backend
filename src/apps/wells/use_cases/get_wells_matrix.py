"""Build a matrix of wells under a given NGDU.

Resolution chain:
  1. Load the NGDU-typed ``Org`` by local id → collect all descendant orgs
     via ``parent_id`` (stored as ABAI id).
  2. Query ABAI ``well_org`` for rows whose ``org`` is in that descendant
     set → group by well, pick the current one (latest ``dbeg``).
  3. Include only wells whose current well_org is still within the NGDU
     subtree (i.e. the well hasn't been moved elsewhere).
  4. Fetch local ``Well``s by their ABAI ids; mark ``is_on_repair`` when
     an unfinished ``Repair`` exists for the same ``abai_well_id``.
"""

from collections import defaultdict
from datetime import date

from apps.org.models.org import Org
from apps.org.repositories.org import OrgRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.dto.internal.well import WellMatrixItemDTO
from apps.wells.dto.queries.well import GetWellsMatrixQuery
from apps.wells.repositories.well import WellRepository
from shared.integrations.abai.models import WellOrg
from shared.integrations.abai.repositories.well_orgs import ABAIWellOrgRepository


class GetWellsMatrixUseCase:
    def __init__(
        self,
        *,
        org_repository: OrgRepository,
        well_repository: WellRepository,
        repair_repository: RepairRepository,
        abai_well_org_repository: ABAIWellOrgRepository,
    ) -> None:
        self.org_repository = org_repository
        self.well_repository = well_repository
        self.repair_repository = repair_repository
        self.abai_well_org_repository = abai_well_org_repository

    async def execute(
        self,
        query: GetWellsMatrixQuery,
    ) -> list[WellMatrixItemDTO]:
        ngdu = await self.org_repository.get_by_id(query.ngdu_id)
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
        wells = [w for w in wells if not w.is_deleted]
        if not wells:
            return []

        active_repairs = await self.repair_repository.list_active_by_well_abai_ids(
            [w.abai_id for w in wells],
        )
        on_repair = {r.abai_well_id for r in active_repairs}

        return [
            WellMatrixItemDTO(
                id=w.id,
                name=w.name,
                is_on_repair=w.abai_id in on_repair,
            )
            for w in sorted(wells, key=lambda w: w.name)
        ]

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
