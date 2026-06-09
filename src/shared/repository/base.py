from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, overload

from pydantic import BaseModel
from sqlalchemy import (
    RowMapping,
    Select,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Load
from sqlalchemy.sql.elements import ColumnElement

from core import get_logger
from shared.errors import AppError
from shared.repository.errors import FiltersIsNotSetError

logger = get_logger(__name__)


class ModelNotSetError(AppError):
    message = "Model is not set."
    code = "model_not_set"


class PageNotSetError(AppError):
    message = "Page is not set."
    code = "page_not_set"


class EmptyUpdateDataError(AppError):
    message = "Update data is empty."
    code = "empty_update_data"


@dataclass(frozen=True, slots=True)
class QuerySpec:
    filters: Sequence[ColumnElement[bool]] = ()
    options: Sequence[Load] = ()
    order_by: Sequence[InstrumentedAttribute[Any]] = ()
    page: int | None = None
    page_size: int | None = None


@dataclass(frozen=True, slots=True)
class JoinSpec:
    target: Any
    onclause: ColumnElement[bool] | None = None
    isouter: bool = False


@dataclass(frozen=True, slots=True)
class JoinQuerySpec:
    joins: Sequence[JoinSpec]
    fields: Sequence[InstrumentedAttribute[Any]]
    filters: Sequence[ColumnElement[bool]] = ()
    order_by: Sequence[InstrumentedAttribute[Any]] = ()
    page: int | None = None
    page_size: int | None = None


class AsyncAlchemyRepository[
    CreateDataT: BaseModel,
    UpdateDataT: BaseModel,
    ModelT: DeclarativeBase,
](ABC):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

        if not hasattr(self, "model"):
            raise ModelNotSetError

        if self.model is DeclarativeBase:
            raise ModelNotSetError

    async def fetch_sequence[T](self, query: Select[tuple[T]]) -> Sequence[T]:
        result = await self.session.execute(query)
        return result.scalars().all()

    async def fetch_all(self, query: Select[Any]) -> Sequence[RowMapping]:
        result = await self.session.execute(query)
        return result.mappings().all()

    async def fetch_one(self, query: Select[Any]) -> RowMapping | None:
        result = await self.session.execute(query)
        return result.mappings().one_or_none()

    @classmethod
    def _apply_joins(
        cls,
        qs: Select,
        joins: Sequence[JoinSpec],
    ) -> Select:
        for join in joins:
            if join.onclause is None:
                qs = qs.join(join.target, isouter=join.isouter)
            else:
                qs = qs.join(join.target, join.onclause, isouter=join.isouter)

        return qs

    @classmethod
    def _apply_filters(
        cls,
        qs: Select,
        filters: Sequence[ColumnElement[bool]],
        *,
        required: bool = False,
    ) -> Select:
        if not filters and required:
            raise FiltersIsNotSetError

        return qs.where(*filters)

    @classmethod
    def _apply_options(
        cls,
        qs: Select,
        options: Sequence[Load],
    ) -> Select:
        if not options:
            return qs

        return qs.options(*options)

    @classmethod
    def _apply_order_by(
        cls,
        qs: Select,
        order_by: Sequence[ColumnElement[Any]],
    ) -> Select:
        if not order_by:
            return qs

        return qs.order_by(*order_by)

    @classmethod
    def _apply_pagination(
        cls,
        qs: Select,
        *,
        page: int | None,
        page_size: int | None,
    ) -> Select:
        if page is None and page_size is None:
            return qs

        if page is None or page_size is None:
            raise PageNotSetError(
                details={
                    "page": page,
                    "page_size": page_size,
                },
            )

        if page < 1 or page_size < 1:
            raise PageNotSetError(
                details={
                    "page": page,
                    "page_size": page_size,
                    "message": "page and page_size must be greater than 0.",
                },
            )

        offset = (page - 1) * page_size
        return qs.limit(page_size).offset(offset)

    def build_select(
        self,
        spec: QuerySpec | JoinQuerySpec | None = None,
        *,
        required_filters: bool = False,
    ) -> Select:
        spec = spec or QuerySpec()

        if isinstance(spec, JoinQuerySpec):
            qs = select(*spec.fields)
            qs = self._apply_joins(qs, spec.joins)
        else:
            qs = select(self.model)
            qs = self._apply_options(qs, spec.options)
        qs = self._apply_filters(qs, spec.filters, required=required_filters)
        qs = self._apply_order_by(qs, spec.order_by)
        qs = self._apply_pagination(qs, page=spec.page, page_size=spec.page_size)
        logger.debug("Built select query: %s", qs)
        return qs

    async def create(self, data: CreateDataT) -> ModelT:
        qs = insert(self.model).values(**data.model_dump()).returning(self.model)

        result = await self.session.execute(qs)
        await self.session.flush()

        return result.scalar_one()

    async def update(
        self,
        data: UpdateDataT,
        filters: Sequence[ColumnElement[bool]],
        *,
        partial: bool = True,
    ) -> ModelT:
        values = data.model_dump(exclude_unset=partial)

        if not values:
            raise EmptyUpdateDataError

        if not filters:
            raise FiltersIsNotSetError

        qs = update(self.model).where(*filters).values(**values).returning(self.model)

        result = await self.session.execute(qs)
        await self.session.flush()

        return result.scalar_one()

    async def delete(
        self,
        filters: Sequence[ColumnElement[bool]],
    ) -> None:
        if not filters:
            raise FiltersIsNotSetError

        qs = delete(self.model).where(*filters)

        await self.session.execute(qs)
        await self.session.flush()

    @overload
    async def get_one(
        self,
        spec: JoinQuerySpec,
    ) -> RowMapping | None: ...
    @overload
    async def get_one(
        self,
        spec: QuerySpec,
    ) -> ModelT | None: ...
    @overload
    async def get_one(
        self,
        spec: None = None,
    ) -> ModelT | None: ...

    async def get_one(
        self,
        spec: QuerySpec | JoinQuerySpec | None = None,
    ) -> ModelT | RowMapping | None:
        qs = self.build_select(spec, required_filters=True)
        result = await self.session.execute(qs)
        if isinstance(spec, JoinQuerySpec):
            return result.mappings().one_or_none()
        return result.scalars().unique().one_or_none()

    @overload
    async def get_list(
        self,
        spec: JoinQuerySpec,
    ) -> Sequence[RowMapping]: ...
    @overload
    async def get_list(
        self,
        spec: QuerySpec,
    ) -> Sequence[ModelT]: ...
    @overload
    async def get_list(
        self,
        spec: None = None,
    ) -> Sequence[ModelT]: ...

    async def get_list(
        self,
        spec: QuerySpec | JoinQuerySpec | None = None,
    ) -> Sequence[ModelT] | Sequence[RowMapping]:
        qs = self.build_select(
            spec,
            required_filters=False,
        )

        result = await self.session.execute(qs)
        if isinstance(spec, JoinQuerySpec):
            return result.mappings().all()
        return result.scalars().unique().all()
