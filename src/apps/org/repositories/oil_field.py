from collections.abc import Sequence

from sqlalchemy import insert

from apps.org.dto.internal.repositories.oil_field import (
    CreateOilFieldDTO,
    UpdateOilFieldDTO,
)
from apps.org.models.oil_field import OilField
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class OilFieldRepository(
    AsyncAlchemyRepository[CreateOilFieldDTO, UpdateOilFieldDTO, OilField],
):
    model = OilField

    async def get_by_id(self, oil_field_id: int) -> OilField | None:
        return await self.get_one(
            QuerySpec(
                filters=(OilField.id == oil_field_id,),
            ),
        )

    async def list_oil_fields(
        self,
        *,
        ngdu_id: int | None = None,
    ) -> Sequence[OilField]:
        """Все месторождения или только относящиеся к НГДУ, по префиксу."""
        filters = (OilField.ngdu_id == ngdu_id,) if ngdu_id is not None else ()
        return await self.get_list(
            QuerySpec(
                filters=filters,
                order_by=(OilField.prefix, OilField.id),
            ),
        )

    async def list_by_prefix(self, prefix: str) -> Sequence[OilField]:
        return await self.get_list(
            QuerySpec(
                filters=(OilField.prefix == prefix,),
                order_by=(OilField.ngdu_id,),
            ),
        )

    async def batch_create(self, data: Sequence[CreateOilFieldDTO]) -> None:
        if not data:
            return

        values = [item.model_dump() for item in data]
        await self.session.execute(insert(OilField), values)

    async def update_by_id(
        self,
        oil_field_id: int,
        data: UpdateOilFieldDTO,
    ) -> OilField:
        return await self.update(
            data=data,
            filters=(OilField.id == oil_field_id,),
        )

    async def delete_by_id(self, oil_field_id: int) -> None:
        await self.delete(filters=(OilField.id == oil_field_id,))
