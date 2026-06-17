from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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
from sqlalchemy.orm import DeclarativeBase, Load
from sqlalchemy.sql.elements import ColumnElement

from core import get_logger
from shared.errors import AppError
from shared.repository.errors import FiltersIsNotSetError

logger = get_logger(__name__)


class ModelNotSetError(AppError):
    """Raised when a repository subclass does not define a concrete SQLAlchemy model."""

    message = "Model is not set."
    code = "model_not_set"


class PageNotSetError(AppError):
    """Raised when pagination arguments are incomplete or invalid."""

    message = "Page is not set."
    code = "page_not_set"


class EmptyUpdateDataError(AppError):
    """Raised when update input does not contain any values to persist."""

    message = "Update data is empty."
    code = "empty_update_data"


class LimitOffsetError(AppError):
    """Raised when explicit limit/offset arguments are invalid."""

    message = "Limit/offset is invalid."
    code = "limit_offset_invalid"


FILTERS_TYPE = Sequence[ColumnElement[bool]]
ORDER_BY_TYPE = Sequence[ColumnElement[Any]]
FIELDS_TYPE = Sequence[Any]


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """Specification for selecting full ORM model instances.

    Use this spec when the query should return instances of the repository model,
    for example ``User`` objects from ``select(User)``.

    Attributes:
        filters: SQLAlchemy boolean expressions used in the WHERE clause.
        options: SQLAlchemy ORM loading options, such as ``selectinload`` or
            ``joinedload``.
        order_by: SQLAlchemy expressions used in the ORDER BY clause.
        page: One-based page number for pagination.
        page_size: Number of rows per page.
        limit: Explicit SQL LIMIT. Cannot be combined with page/page_size.
        offset: Explicit SQL OFFSET. Cannot be combined with page/page_size.
    """

    filters: FILTERS_TYPE = ()
    options: Sequence[Load] = ()
    order_by: ORDER_BY_TYPE = ()
    page: int | None = None
    page_size: int | None = None
    limit: int | None = None
    offset: int | None = None


@dataclass(frozen=True, slots=True)
class JoinSpec:
    """Specification for a SQL JOIN clause.

    Attributes:
        target: SQLAlchemy ORM model, relationship, table, alias, or selectable
            to join against.
        onclause: Optional explicit ON condition. If omitted, SQLAlchemy will try
            to infer the join condition from relationships or foreign keys.
        isouter: Whether to use LEFT OUTER JOIN instead of INNER JOIN.
    """

    target: Any
    onclause: ColumnElement[bool] | None = None
    isouter: bool = False


@dataclass(frozen=True, slots=True)
class ProjectionQuerySpec:
    """Specification for selecting custom fields instead of full ORM models.

    Use this spec for read-model queries, joins, reports, aggregates, DTO-like
    selections, and other cases where the result should be a ``RowMapping``
    rather than a model instance.

    Example:
        ``select(User.id, User.email, Profile.avatar_url)``

    Attributes:
        joins: JOIN clauses applied to the query.
        fields: SQLAlchemy fields or expressions selected by the query.
        filters: SQLAlchemy boolean expressions used in the WHERE clause.
        order_by: SQLAlchemy expressions used in the ORDER BY clause.
        page: One-based page number for pagination.
        page_size: Number of rows per page.
        limit: Explicit SQL LIMIT. Cannot be combined with page/page_size.
        offset: Explicit SQL OFFSET. Cannot be combined with page/page_size.
    """

    joins: Sequence[JoinSpec]
    fields: FIELDS_TYPE
    filters: FILTERS_TYPE = ()
    order_by: ORDER_BY_TYPE = ()
    page: int | None = None
    page_size: int | None = None
    limit: int | None = None
    offset: int | None = None


class AsyncAlchemyRepository[
    CreateDataT: BaseModel,
    UpdateDataT: BaseModel,
    ModelT: DeclarativeBase,
](ABC):
    """Base async repository for SQLAlchemy ORM models.

    The repository encapsulates common CRUD operations and query-building logic.
    It does not commit transactions by itself; transaction boundaries should be
    controlled by a service layer or Unit of Work.

    Type parameters:
        CreateDataT: Pydantic schema used for create operations.
        UpdateDataT: Pydantic schema used for update operations.
        ModelT: SQLAlchemy ORM model handled by this repository.

    Subclasses must define:
        model: Concrete SQLAlchemy ORM model class.

    Example:
        class UserRepository(
            AsyncAlchemyRepository[UserCreateSchema, UserUpdateSchema, User]
        ):
            model = User
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with an async SQLAlchemy session.

        Args:
            session: Active ``AsyncSession`` used to execute queries.

        Raises:
            ModelNotSetError: If subclass does not define a concrete model.
        """

        self.session = session

        if not hasattr(self, "model") or self.model is DeclarativeBase:
            raise ModelNotSetError

    async def fetch_scalars[T](self, query: Select[Any]) -> Sequence[T]:
        """Execute a query and return scalar results.

        This helper is useful for queries like ``select(User.id)`` or
        ``select(User)`` when only the first selected entity/column is needed.

        Args:
            query: SQLAlchemy SELECT query.

        Returns:
            Sequence of scalar values returned by SQLAlchemy.
        """

        result = await self.session.execute(query)
        return result.scalars().all()

    async def fetch_all(self, query: Select[Any]) -> Sequence[RowMapping]:
        """Execute a query and return all rows as mappings.

        Args:
            query: SQLAlchemy SELECT query.

        Returns:
            Sequence of ``RowMapping`` objects.
        """

        result = await self.session.execute(query)
        return result.mappings().all()

    async def fetch_one(self, query: Select[Any]) -> RowMapping | None:
        """Execute a query and return exactly one row mapping or ``None``.

        Args:
            query: SQLAlchemy SELECT query.

        Returns:
            One ``RowMapping`` if exactly one row exists, otherwise ``None``.

        Raises:
            sqlalchemy.exc.MultipleResultsFound: If the query returns more than
                one row.
        """

        result = await self.session.execute(query)
        return result.mappings().one_or_none()

    @classmethod
    def _apply_joins(
        cls,
        qs: Select[Any],
        joins: Sequence[JoinSpec],
    ) -> Select[Any]:
        """Apply JOIN clauses to a SELECT query.

        Args:
            qs: Base SQLAlchemy SELECT query.
            joins: Join specifications to apply.

        Returns:
            SELECT query with JOIN clauses applied.
        """

        for join in joins:
            if join.onclause is None:
                qs = qs.join(join.target, isouter=join.isouter)
            else:
                qs = qs.join(join.target, join.onclause, isouter=join.isouter)

        return qs

    @classmethod
    def _apply_filters(
        cls,
        qs: Select[Any],
        filters: FILTERS_TYPE,
        *,
        required: bool = False,
    ) -> Select[Any]:
        """Apply WHERE filters to a SELECT query.

        Args:
            qs: Base SQLAlchemy SELECT query.
            filters: SQLAlchemy boolean expressions.
            required: Whether at least one filter is required.

        Returns:
            SELECT query with WHERE filters applied.

        Raises:
            FiltersIsNotSetError: If ``required`` is true and filters are empty.
        """

        if required and not filters:
            raise FiltersIsNotSetError

        return qs.where(*filters)

    @classmethod
    def _apply_options(
        cls,
        qs: Select[Any],
        options: Sequence[Load],
    ) -> Select[Any]:
        """Apply SQLAlchemy ORM loading options to a SELECT query.

        Args:
            qs: Base SQLAlchemy SELECT query.
            options: ORM loading options such as ``selectinload`` or
                ``joinedload``.

        Returns:
            SELECT query with ORM options applied.
        """

        if not options:
            return qs

        return qs.options(*options)

    @classmethod
    def _apply_order_by(
        cls,
        qs: Select[Any],
        order_by: ORDER_BY_TYPE,
    ) -> Select[Any]:
        """Apply ORDER BY expressions to a SELECT query.

        Args:
            qs: Base SQLAlchemy SELECT query.
            order_by: SQLAlchemy ordering expressions.

        Returns:
            SELECT query with ORDER BY clause applied.
        """

        if not order_by:
            return qs

        return qs.order_by(*order_by)

    @classmethod
    def _apply_pagination(
        cls,
        qs: Select[Any],
        *,
        page: int | None,
        page_size: int | None,
    ) -> Select[Any]:
        """Apply limit-offset pagination to a SELECT query.

        Pagination uses one-based page numbers.

        Args:
            qs: Base SQLAlchemy SELECT query.
            page: One-based page number.
            page_size: Number of rows per page.

        Returns:
            SELECT query with LIMIT and OFFSET applied.

        Raises:
            PageNotSetError: If only one pagination argument is set or if any
                pagination value is less than 1.
        """

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

    @classmethod
    def _apply_limit_offset(
        cls,
        qs: Select[Any],
        *,
        limit: int | None,
        offset: int | None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Select[Any]:
        """Apply explicit LIMIT/OFFSET to a SELECT query.

        Explicit limit/offset cannot be combined with page/page_size pagination,
        because page/page_size already produces LIMIT/OFFSET.

        Args:
            qs: Base SQLAlchemy SELECT query.
            limit: Explicit SQL LIMIT.
            offset: Explicit SQL OFFSET.
            page: One-based page number for pagination.
            page_size: Number of rows per page.

        Returns:
            SELECT query with explicit LIMIT/OFFSET applied.

        Raises:
            LimitOffsetError: If limit/offset values are invalid or combined with
                page/page_size pagination.
        """

        if limit is None and offset is None:
            return qs

        if page is not None or page_size is not None:
            raise LimitOffsetError(
                details={
                    "page": page,
                    "page_size": page_size,
                    "limit": limit,
                    "offset": offset,
                    "message": "limit/offset cannot be combined with page/page_size.",
                },
            )

        if limit is not None and limit < 1:
            raise LimitOffsetError(
                details={
                    "limit": limit,
                    "message": "limit must be greater than 0.",
                },
            )

        if offset is not None and offset < 0:
            raise LimitOffsetError(
                details={
                    "offset": offset,
                    "message": "offset must be greater than or equal to 0.",
                },
            )

        if limit is not None:
            qs = qs.limit(limit)

        if offset is not None:
            qs = qs.offset(offset)

        return qs

    def build_model_select(
        self,
        spec: QuerySpec | None = None,
        *,
        required_filters: bool = False,
    ) -> Select[Any]:
        """Build a SELECT query that returns full ORM model instances.

        Args:
            spec: Query specification. If omitted, an empty ``QuerySpec`` is
                used.
            required_filters: Whether the query must contain at least one
                filter.

        Returns:
            SQLAlchemy SELECT query for the repository model.

        Raises:
            FiltersIsNotSetError: If filters are required but not provided.
            PageNotSetError: If pagination arguments are invalid.
        """

        spec = spec or QuerySpec()

        qs = select(self.model)
        qs = self._apply_options(qs, spec.options)
        qs = self._apply_filters(qs, spec.filters, required=required_filters)
        qs = self._apply_order_by(qs, spec.order_by)
        qs = self._apply_pagination(qs, page=spec.page, page_size=spec.page_size)
        qs = self._apply_limit_offset(
            qs,
            limit=spec.limit,
            offset=spec.offset,
            page=spec.page,
            page_size=spec.page_size,
        )

        logger.debug("Built model select query: %s", qs)

        return qs

    def build_projection_select(
        self,
        spec: ProjectionQuerySpec,
        *,
        required_filters: bool = False,
    ) -> Select[Any]:
        """Build a SELECT query that returns custom fields as row mappings.

        Args:
            spec: Projection query specification.
            required_filters: Whether the query must contain at least one
                filter.

        Returns:
            SQLAlchemy SELECT query for selected fields or expressions.

        Raises:
            FiltersIsNotSetError: If filters are required but not provided.
            PageNotSetError: If pagination arguments are invalid.
        """

        qs = select(*spec.fields)
        qs = self._apply_joins(qs, spec.joins)
        qs = self._apply_filters(qs, spec.filters, required=required_filters)
        qs = self._apply_order_by(qs, spec.order_by)
        qs = self._apply_pagination(qs, page=spec.page, page_size=spec.page_size)
        qs = self._apply_limit_offset(
            qs,
            limit=spec.limit,
            offset=spec.offset,
            page=spec.page,
            page_size=spec.page_size,
        )

        logger.debug("Built projection select query: %s", qs)

        return qs

    async def create(
        self,
        data: CreateDataT,
        *,
        exclude_none: bool = False,
    ) -> ModelT:
        """Create a new model instance.

        The method executes INSERT ... RETURNING and returns the created ORM
        model. It does not commit the transaction.

        Args:
            data: Pydantic schema containing values for creation.
            exclude_none: Whether to exclude fields with ``None`` values from
                the INSERT statement.

        Returns:
            Created ORM model instance.
        """

        values = data.model_dump(exclude_none=exclude_none)

        qs = insert(self.model).values(**values).returning(self.model)
        result = await self.session.execute(qs)

        return result.scalar_one()

    async def bulk_create(self, data: Sequence[CreateDataT]) -> None:
        if not data:
            return

        values = [item.model_dump() for item in data]
        await self.session.execute(insert(self.model), values)

    async def update(
        self,
        data: UpdateDataT,
        filters: FILTERS_TYPE,
        *,
        partial: bool = True,
        exclude_none: bool = False,
    ) -> ModelT:
        """Update one model instance matched by filters.

        The method executes UPDATE ... RETURNING and returns the updated ORM
        model. It requires filters to prevent accidental mass updates. It does
        not commit the transaction.

        Args:
            data: Pydantic schema containing update values.
            filters: SQLAlchemy boolean expressions used in the WHERE clause.
            partial: Whether to exclude fields that were not explicitly set in
                the Pydantic schema.
            exclude_none: Whether to exclude fields with ``None`` values from
                the UPDATE statement.

        Returns:
            Updated ORM model instance.

        Raises:
            EmptyUpdateDataError: If there are no values to update.
            FiltersIsNotSetError: If filters are empty.
            sqlalchemy.exc.NoResultFound: If no row matches the filters.
            sqlalchemy.exc.MultipleResultsFound: If multiple rows are returned.
        """

        values = data.model_dump(
            exclude_unset=partial,
            exclude_none=exclude_none,
        )

        if not values:
            raise EmptyUpdateDataError

        if not filters:
            raise FiltersIsNotSetError

        qs = update(self.model).where(*filters).values(**values).returning(self.model)

        result = await self.session.execute(qs)

        return result.scalar_one()

    async def delete(
        self,
        filters: FILTERS_TYPE,
    ) -> None:
        """Delete rows matched by filters.

        Filters are required to prevent accidental deletion of all rows. The
        method does not commit the transaction.

        Args:
            filters: SQLAlchemy boolean expressions used in the WHERE clause.

        Raises:
            FiltersIsNotSetError: If filters are empty.
        """

        if not filters:
            raise FiltersIsNotSetError

        qs = delete(self.model).where(*filters)

        await self.session.execute(qs)

    # async def get_by_id(
    #     self,
    #     id_: Any,
    #     id_field: InstrumentedAttribute[Any] | None = None,
    #     *,
    #     options: Sequence[Load] = (),
    # ) -> ModelT | None:
    #     """Return one model instance by primary key or another identifier field.
    #
    #     Args:
    #         id_: Identifier value.
    #         id_field: SQLAlchemy model attribute used as the identifier. If
    #             omitted, ``self.model.id`` is used.
    #         options: ORM loading options such as ``selectinload`` or
    #             ``joinedload``.
    #
    #     Returns:
    #         Matching ORM model instance or ``None``.
    #     """
    #
    #     field = id_field or self.model.id
    #
    #     spec = QuerySpec(
    #         filters=(field == id_,),
    #         options=options,
    #     )
    #
    #     return await self.get_one(spec)

    async def get_one(
        self,
        spec: QuerySpec | None = None,
        *,
        required_filters: bool = True,
    ) -> ModelT | None:
        """Return one full ORM model instance.

        Args:
            spec: Query specification. If omitted, an empty ``QuerySpec`` is
                used.
            required_filters: Whether at least one filter is required.

        Returns:
            Matching ORM model instance or ``None``.

        Raises:
            FiltersIsNotSetError: If filters are required but not provided.
            sqlalchemy.exc.MultipleResultsFound: If the query returns more than
                one row.
        """

        qs = self.build_model_select(
            spec,
            required_filters=required_filters,
        )

        result = await self.session.execute(qs)

        return result.scalars().one_or_none()

    async def get_list(
        self,
        spec: QuerySpec | None = None,
    ) -> Sequence[ModelT]:
        """Return a list of full ORM model instances.

        Args:
            spec: Query specification. If omitted, an empty ``QuerySpec`` is
                used.

        Returns:
            Sequence of ORM model instances.
        """

        qs = self.build_model_select(
            spec,
            required_filters=False,
        )

        result = await self.session.execute(qs)

        return result.scalars().all()

    async def get_projection_one(
        self,
        spec: ProjectionQuerySpec,
        *,
        required_filters: bool = True,
    ) -> RowMapping | None:
        """Return one projection row as a mapping.

        Args:
            spec: Projection query specification.
            required_filters: Whether at least one filter is required.

        Returns:
            Matching ``RowMapping`` or ``None``.

        Raises:
            FiltersIsNotSetError: If filters are required but not provided.
            sqlalchemy.exc.MultipleResultsFound: If the query returns more than
                one row.
        """

        qs = self.build_projection_select(
            spec,
            required_filters=required_filters,
        )

        result = await self.session.execute(qs)

        return result.mappings().one_or_none()

    async def get_projection_list(
        self,
        spec: ProjectionQuerySpec,
    ) -> Sequence[RowMapping]:
        """Return projection rows as mappings.

        Args:
            spec: Projection query specification.

        Returns:
            Sequence of ``RowMapping`` objects.
        """

        qs = self.build_projection_select(
            spec,
            required_filters=False,
        )

        result = await self.session.execute(qs)

        return result.mappings().all()
