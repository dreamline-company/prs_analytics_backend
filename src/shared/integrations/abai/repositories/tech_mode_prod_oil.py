from collections.abc import Sequence

from shared.integrations.abai.models import TechModeProdOil
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAITechModeProdOilRepository(
    ABAIReadOnlyRepository[TechModeProdOil],
):
    model = TechModeProdOil

    async def list_by_ids(
        self,
        tech_mode_prod_oil_ids: Sequence[int],
    ) -> Sequence[TechModeProdOil]:
        if not tech_mode_prod_oil_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(TechModeProdOil.id.in_(tech_mode_prod_oil_ids),),
                order_by=(TechModeProdOil.id,),
            ),
        )

    async def list_after_id(
        self,
        tech_mode_prod_oil_id: int,
    ) -> Sequence[TechModeProdOil]:
        return await self.get_list(
            QuerySpec(
                filters=(TechModeProdOil.id > tech_mode_prod_oil_id,),
                order_by=(TechModeProdOil.id,),
            ),
        )

    async def list_by_well(self, well_id: int) -> Sequence[TechModeProdOil]:
        return await self.get_list(
            QuerySpec(
                filters=(TechModeProdOil.well == well_id,),
                order_by=(TechModeProdOil.dbeg,),
            ),
        )

    async def list_by_wells(
        self,
        well_ids: Sequence[int],
    ) -> Sequence[TechModeProdOil]:
        if not well_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(TechModeProdOil.well.in_(well_ids),),
                order_by=(TechModeProdOil.well, TechModeProdOil.dbeg),
            ),
        )
