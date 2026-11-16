from abc import ABC
from collections.abc import Sequence

from sqlalchemy.orm import DeclarativeBase

from shared.dto.repositories import RepositoryDTO
from shared.errors import AppError
from shared.repository.sqlalchemy import (
    FILTERS_TYPE,
    AsyncAlchemyRepository,
    QuerySpec,
)


class ABAIRepositoryIsReadOnlyError(AppError):
    message = "ABAI repository is read-only."
    code = "abai_repository_is_read_only"


class ABAIReadOnlyRepository[ModelT: DeclarativeBase](
    AsyncAlchemyRepository[RepositoryDTO, RepositoryDTO, ModelT],
    ABC,
):
    async def create(
        self,
        data: RepositoryDTO,
        *,
        exclude_none: bool = False,
    ) -> ModelT:
        _ = data, exclude_none
        raise ABAIRepositoryIsReadOnlyError

    async def update(
        self,
        data: RepositoryDTO,
        filters: FILTERS_TYPE,
        *,
        partial: bool = True,
        exclude_none: bool = False,
    ) -> ModelT:
        _ = data, filters, partial, exclude_none
        raise ABAIRepositoryIsReadOnlyError

    async def delete(
        self,
        filters: FILTERS_TYPE,
    ) -> None:
        _ = filters
        raise ABAIRepositoryIsReadOnlyError


class ABAIKeysetRepository[ModelT: DeclarativeBase](ABAIReadOnlyRepository[ModelT]):
    """Таблица ABAI, которая читается целиком порциями по ``id``.

    Для справочников и привязок на десятки тысяч строк, которые зеркала
    сверяют с источником целиком за прогон. Модели нужен целочисленный ``id``.
    """

    BATCH_SIZE = 5000

    async def list_all(self) -> list[ModelT]:
        rows: list[ModelT] = []
        last_id = 0
        while True:
            batch = await self.list_after_id(last_id, limit=self.BATCH_SIZE)
            rows.extend(batch)
            if len(batch) < self.BATCH_SIZE:
                return rows
            last_id = batch[-1].id

    async def list_after_id(self, last_id: int, *, limit: int) -> Sequence[ModelT]:
        """Порция строк с id > last_id (keyset-пагинация)."""
        return await self.get_list(
            QuerySpec(
                filters=(self.model.id > last_id,),
                order_by=(self.model.id.asc(),),
                limit=limit,
            ),
        )
