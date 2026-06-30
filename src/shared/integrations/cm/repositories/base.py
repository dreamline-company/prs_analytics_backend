from abc import ABC

from sqlalchemy.orm import DeclarativeBase

from shared.dto.repositories import RepositoryDTO
from shared.errors import AppError
from shared.repository.sqlalchemy import (
    FILTERS_TYPE,
    AsyncAlchemyRepository,
)


class CMRepositoryIsReadOnlyError(AppError):
    message = "CM repository is read-only."
    code = "cm_repository_is_read_only"


class CMReadOnlyRepository[ModelT: DeclarativeBase](
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
        raise CMRepositoryIsReadOnlyError

    async def update(
        self,
        data: RepositoryDTO,
        filters: FILTERS_TYPE,
        *,
        partial: bool = True,
        exclude_none: bool = False,
    ) -> ModelT:
        _ = data, filters, partial, exclude_none
        raise CMRepositoryIsReadOnlyError

    async def delete(
        self,
        filters: FILTERS_TYPE,
    ) -> None:
        _ = filters
        raise CMRepositoryIsReadOnlyError
