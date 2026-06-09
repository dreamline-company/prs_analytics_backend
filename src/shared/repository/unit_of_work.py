from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.errors import AppError


class SqlSessionNotInitedError(AppError):
    """Raised when the Unit of Work session is accessed outside its context."""

    message = "Session is not initialized."
    code = "session_not_inited"


class SqlUnitOfWork(ABC):
    """Base async SQLAlchemy Unit of Work.

    The Unit of Work owns a single SQLAlchemy ``AsyncSession`` during an
    ``async with`` block and coordinates transaction boundaries for repositories.

    This implementation uses explicit commit semantics:

    - call ``commit()`` to persist changes;
    - if an exception happens, changes are rolled back;
    - if ``commit()`` was not called before leaving the context, changes are
      also rolled back.

    Repositories should be created in ``_define_repositories()`` using
    ``self.session``.

    Example:
        class AppUnitOfWork(SqlUnitOfWork):
            def _define_repositories(self) -> None:
                self.users = UserRepository(self.session)
                self.posts = PostRepository(self.session)

        async with AppUnitOfWork(session_factory) as uow:
            await uow.users.create(data)
            await uow.commit()
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the Unit of Work.

        Args:
            session_factory: SQLAlchemy async session factory used to create
                a new ``AsyncSession`` for each Unit of Work context.
        """

        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> Self:
        """Enter the Unit of Work context.

        Creates a new ``AsyncSession``, resets the commit state, initializes
        repositories, and returns the current Unit of Work instance.

        Returns:
            Current Unit of Work instance.
        """

        self._session = self._session_factory()
        self._committed = False
        self._define_repositories()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the Unit of Work context.

        Rolls back the transaction if an exception occurred or if ``commit()``
        was not called. Always closes the session and resets internal state.

        Args:
            exc_type: Exception type raised inside the context, if any.
            exc: Exception instance raised inside the context, if any.
            traceback: Exception traceback, if any.
        """

        if self._session is None:
            return

        try:
            if exc_type is not None or not self._committed:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None
            self._committed = False

    @property
    def session(self) -> AsyncSession:
        """Return the active SQLAlchemy async session.

        Returns:
            Active ``AsyncSession`` bound to the current Unit of Work context.

        Raises:
            SqlSessionNotInitedError: If the session is accessed before entering
                the context or after leaving it.
        """

        if self._session is None:
            raise SqlSessionNotInitedError(details={"action": "Accessing session."})

        return self._session

    @abstractmethod
    def _define_repositories(self) -> None:
        """Initialize repositories bound to the current session.

        Subclasses must assign repository instances here. Each repository should
        receive ``self.session`` so all operations inside the Unit of Work share
        the same transaction.

        Example:
            self.users = UserRepository(self.session)
            self.posts = PostRepository(self.session)
        """

    async def commit(self) -> None:
        """Commit the current transaction.

        Marks the Unit of Work as committed so ``__aexit__`` will not roll back
        the transaction on successful context exit.

        Raises:
            SqlSessionNotInitedError: If called outside the Unit of Work context.
        """

        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Rollback the current transaction.

        Resets the committed flag. After calling this method, the Unit of Work
        should usually be considered logically finished, even though the session
        remains technically usable until the context exits.

        Raises:
            SqlSessionNotInitedError: If called outside the Unit of Work context.
        """

        await self.session.rollback()
        self._committed = False
