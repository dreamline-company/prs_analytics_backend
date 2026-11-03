"""Зеркало технологических объектов ABAI: ГЗУ и привязки к ним скважин

Revision ID: eb3543da89ed
Revises: 6d04c431ed5c
Create Date: 2026-11-03 16:44:43.821029

Копии emg.tech_type, emg.tech, emg.tech_history и emg.well_tech: на какой ГЗУ
и каком отводе стоит скважина. Заполняет таска wells.tech.sync.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eb3543da89ed"
down_revision: str | None = "6d04c431ed5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_tech_type",
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_tech_type")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_tech_type_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_tech_type_id"),
        "wells_tech_type",
        ["id"],
        unique=False,
    )
    op.create_table(
        "wells_tech",
        sa.Column("abai_tech_type_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_tech_type_id"],
            ["wells_tech_type.abai_id"],
            name=op.f("fk_wells_tech_abai_tech_type_id_wells_tech_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_tech")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_tech_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_tech_abai_tech_type_id"),
        "wells_tech",
        ["abai_tech_type_id"],
        unique=False,
    )
    op.create_index(op.f("ix_wells_tech_id"), "wells_tech", ["id"], unique=False)
    op.create_table(
        "wells_tech_history",
        sa.Column("abai_tech_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_parent_id", sa.BigInteger(), nullable=True),
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("dbeg", sa.Date(), nullable=True),
        sa.Column("dend", sa.Date(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_tech_id"],
            ["wells_tech.abai_id"],
            name=op.f("fk_wells_tech_history_abai_tech_id_wells_tech"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_tech_history")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_tech_history_abai_id")),
    )
    op.create_index(
        "ix_wells_tech_history_abai_tech_id_dbeg",
        "wells_tech_history",
        ["abai_tech_id", "dbeg"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_tech_history_id"),
        "wells_tech_history",
        ["id"],
        unique=False,
    )
    op.create_table(
        "wells_well_tech",
        sa.Column("abai_well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_tech_id", sa.BigInteger(), nullable=False),
        sa.Column("tap", sa.Integer(), nullable=True),
        sa.Column("dbeg", sa.Date(), nullable=True),
        sa.Column("dend", sa.Date(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_tech_id"],
            ["wells_tech.abai_id"],
            name=op.f("fk_wells_well_tech_abai_tech_id_wells_tech"),
        ),
        sa.ForeignKeyConstraint(
            ["abai_well_id"],
            ["wells_well.abai_id"],
            name=op.f("fk_wells_well_tech_abai_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_tech")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_tech_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_well_tech_abai_tech_id"),
        "wells_well_tech",
        ["abai_tech_id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_tech_abai_well_id_dbeg",
        "wells_well_tech",
        ["abai_well_id", "dbeg"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_tech_id"),
        "wells_well_tech",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wells_well_tech_id"), table_name="wells_well_tech")
    op.drop_index("ix_wells_well_tech_abai_well_id_dbeg", table_name="wells_well_tech")
    op.drop_index(op.f("ix_wells_well_tech_abai_tech_id"), table_name="wells_well_tech")
    op.drop_table("wells_well_tech")
    op.drop_index(op.f("ix_wells_tech_history_id"), table_name="wells_tech_history")
    op.drop_index(
        "ix_wells_tech_history_abai_tech_id_dbeg",
        table_name="wells_tech_history",
    )
    op.drop_table("wells_tech_history")
    op.drop_index(op.f("ix_wells_tech_id"), table_name="wells_tech")
    op.drop_index(op.f("ix_wells_tech_abai_tech_type_id"), table_name="wells_tech")
    op.drop_table("wells_tech")
    op.drop_index(op.f("ix_wells_tech_type_id"), table_name="wells_tech_type")
    op.drop_table("wells_tech_type")
