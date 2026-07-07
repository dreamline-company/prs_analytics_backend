"""Resolve the NGDU-typed ``Org`` responsible for a well.

Chain:
  1. Look up current ``well_org`` in ABAI for the given ``abai_well_id``
     (latest ``dbeg`` wins).
  2. Fetch the corresponding local ``Org`` by its ABAI id.
  3. Walk parent chain up the org hierarchy until an ``Org`` with
     ``org_type_id == NGDU_ORG_TYPE`` is found. Parent ids on local ``Org``
     store ABAI ids, so lookups go through ``get_by_abai_id`` at each step.

Returns ``None`` if no well_org row exists, the local mirror is missing,
or the chain runs out before reaching an NGDU node.
"""

from apps.org.models.org import Org
from apps.org.repositories.org import OrgRepository
from core import get_logger
from shared.constants.ngdu import NGDU_ORG_TYPE
from shared.integrations.abai.repositories.well_orgs import ABAIWellOrgRepository

logger = get_logger(__name__)


class GetNGDUForWellUseCase:
    MAX_PARENT_HOPS = 20

    def __init__(
        self,
        abai_well_org_repository: ABAIWellOrgRepository,
        org_repository: OrgRepository,
    ) -> None:
        self.abai_well_org_repository = abai_well_org_repository
        self.org_repository = org_repository

    async def execute(self, abai_well_id: int) -> Org | None:
        well_orgs = await self.abai_well_org_repository.list_by_well(abai_well_id)
        if not well_orgs:
            logger.warning(
                "No well_org rows in ABAI for abai_well_id=%s.",
                abai_well_id,
            )
            return None

        current = self._current_well_org(well_orgs)
        if current is None or current.org is None:
            logger.warning(
                "No current well_org / org for abai_well_id=%s.",
                abai_well_id,
            )
            return None

        org = await self.org_repository.get_by_abai_id(abai_id=current.org)
        if org is None:
            logger.warning(
                "Local Org mirror missing for ABAI org id=%s "
                "(abai_well_id=%s). Sync orgs first.",
                current.org,
                abai_well_id,
            )
            return None

        hops = 0
        while org.org_type_id != NGDU_ORG_TYPE:
            if org.parent_id is None:
                logger.warning(
                    "Org id=%s (abai_id=%s) is not NGDU and has no parent "
                    "(abai_well_id=%s).",
                    org.id,
                    org.abai_id,
                    abai_well_id,
                )
                return None

            hops += 1
            if hops > self.MAX_PARENT_HOPS:
                logger.warning(
                    "Parent-chain depth exceeded %s hops for "
                    "abai_well_id=%s; possible cycle. Aborting.",
                    self.MAX_PARENT_HOPS,
                    abai_well_id,
                )
                return None

            parent = await self.org_repository.get_by_abai_id(abai_id=org.parent_id)
            if parent is None:
                logger.warning(
                    "Parent Org (abai_id=%s) not found while walking up from "
                    "abai_well_id=%s.",
                    org.parent_id,
                    abai_well_id,
                )
                return None
            org = parent

        return org

    @staticmethod
    def _current_well_org(well_orgs):  # noqa: ANN001, ANN205
        with_dbeg = [w for w in well_orgs if w.dbeg is not None]
        if with_dbeg:
            with_dbeg.sort(key=lambda w: w.dbeg, reverse=True)
            return with_dbeg[0]
        return well_orgs[-1]
