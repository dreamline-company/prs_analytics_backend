"""add wells_well_expl_type and wells_well_expl

Revision ID: d1f7b3a95e42
Revises: c9d4e2a6f831
Create Date: 2026-08-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f7b3a95e42"
down_revision: str | None = "c9d4e2a6f831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_well_expl_type",
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("tbd_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_expl_type")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_expl_type_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_well_expl_type_id"),
        "wells_well_expl_type",
        ["id"],
        unique=False,
    )
    op.create_table(
        "wells_well_expl",
        sa.Column("abai_well_id", sa.BigInteger(), nullable=False),
        sa.Column("expl", sa.BigInteger(), nullable=True),
        sa.Column("dbeg", sa.Date(), nullable=True),
        sa.Column("dend", sa.Date(), nullable=True),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_well_id"],
            ["wells_well.abai_id"],
            name=op.f("fk_wells_well_expl_abai_well_id_wells_well"),
        ),
        sa.ForeignKeyConstraint(
            ["expl"],
            ["wells_well_expl_type.abai_id"],
            name=op.f("fk_wells_well_expl_expl_wells_well_expl_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_expl")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_expl_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_well_expl_dend"),
        "wells_well_expl",
        ["dend"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_expl_id"),
        "wells_well_expl",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_expl_abai_well_id_dbeg",
        "wells_well_expl",
        ["abai_well_id", "dbeg"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wells_well_expl_abai_well_id_dbeg",
        table_name="wells_well_expl",
    )
    op.drop_index(op.f("ix_wells_well_expl_id"), table_name="wells_well_expl")
    op.drop_index(op.f("ix_wells_well_expl_dend"), table_name="wells_well_expl")
    op.drop_table("wells_well_expl")
    op.drop_index(
        op.f("ix_wells_well_expl_type_id"),
        table_name="wells_well_expl_type",
    )
    op.drop_table("wells_well_expl_type")
