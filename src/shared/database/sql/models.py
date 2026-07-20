from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, registry

convention = {
    "ix": "ix_%(column_0_label)s",  # INDEX
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",  # UNIQUE
    "ck": "ck_%(table_name)s_%(constraint_name)s",  # CHECK
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",  # FOREIGN KEY
    "pk": "pk_%(table_name)s",  # PRIMARY KEY
}

mapper_registry = registry(metadata=sa.MetaData(naming_convention=convention))


class AppBaseModel(DeclarativeBase):
    """An abstract base model that save metadata, all models must inherit from this class."""

    registry = mapper_registry
    metadata = mapper_registry.metadata

    __abstract__ = True


class UuidPkMixin:
    id: Mapped[UUID] = mapped_column(
        sa.types.Uuid,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        index=True,
    )


class IntPkMixin:
    id: Mapped[int] = mapped_column(
        sa.BigInteger,
        primary_key=True,
        autoincrement=True,
        index=True,
    )


class TimedMixinModel:
    created_at: Mapped[datetime] = mapped_column(
        sa.types.DateTime,
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.types.DateTime,
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class AbaiIdMixin:
    abai_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        unique=True,
        nullable=False,
    )


class ABAIBaseModel(DeclarativeBase):
    """An abstract base model for ABAI DB tables."""

    __table_args__ = {"schema": "emg"}
    __abstract__ = True


class WinccTelemetryBaseModel(DeclarativeBase):
    """An abstract base model for ABAI DB tables."""

    __abstract__ = True


class CMBaseModel(DeclarativeBase):
    """An abstract base model for CM DB tables."""

    __abstract__ = True


class SDMOBaseModel(DeclarativeBase):
    """An abstract base model for SDMO DB tables."""

    __table_args__ = {"schema": "sdmo"}
    __abstract__ = True
