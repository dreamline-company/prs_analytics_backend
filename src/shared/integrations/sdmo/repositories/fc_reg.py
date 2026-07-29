from collections.abc import Sequence

from shared.integrations.sdmo.models import FcReg
from shared.integrations.sdmo.repositories.base import SDMOReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class SDMOFcRegRepository(
    SDMOReadOnlyRepository[FcReg],
):
    model = FcReg

    async def get_by_addr(
        self,
        addr: int,
        type_1900: int | None = None,
    ) -> FcReg | None:
        filters = [FcReg.addr == addr]
        if type_1900 is not None:
            filters.append(FcReg.type_1900 == type_1900)

        return await self.get_one(
            QuerySpec(
                filters=tuple(filters),
            ),
        )

    async def list_by_type_1900(self, type_1900: int) -> Sequence[FcReg]:
        return await self.get_list(
            QuerySpec(
                filters=(FcReg.type_1900 == type_1900,),
                order_by=(FcReg.addr,),
            ),
        )

    async def list_dynamic(self, type_1900: int | None = None) -> Sequence[FcReg]:
        filters = [FcReg.dynamic.is_(True)]
        if type_1900 is not None:
            filters.append(FcReg.type_1900 == type_1900)

        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(FcReg.addr,),
            ),
        )

    async def list_all(self) -> Sequence[FcReg]:
        return await self.get_list(
            QuerySpec(
                order_by=(FcReg.type_1900, FcReg.addr),
            ),
        )
