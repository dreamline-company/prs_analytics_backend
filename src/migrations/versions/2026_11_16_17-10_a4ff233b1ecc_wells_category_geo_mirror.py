"""Зеркало категорий и горизонтов скважин ABAI

Revision ID: a4ff233b1ecc
Revises: 6d5383b26f01
Create Date: 2026-11-16 17:10:15.842827

Копии emg.well_category_type, emg.well_category, emg.geo_type, emg.geo и
emg.well_geo: категория скважины (нефтяная, нагнетательная, наблюдательная…)
и горизонты, на которые она заведена, по периодам. Заполняет таска
wells.category_geo.sync.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4ff233b1ecc"
down_revision: str | None = "6d5383b26f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _reference_table(name: str) -> None:
    """Справочник ABAI: id, abai_id, названия и код."""
    op.create_table(
        name,
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
        sa.UniqueConstraint("abai_id", name=op.f(f"uq_{name}_abai_id")),
    )
    op.create_index(op.f(f"ix_{name}_id"), name, ["id"], unique=False)


def upgrade() -> None:
    """Upgrade schema."""
    _reference_table("wells_well_category_type")
    _reference_table("wells_geo_type")

    op.create_table(
        "wells_well_category",
        sa.Column("abai_well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_category_type_id", sa.BigInteger(), nullable=False),
        sa.Column("dbeg", sa.DateTime(), nullable=True),
        sa.Column("dend", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_category_type_id"],
            ["wells_well_category_type.abai_id"],
            name=op.f(
                "fk_wells_well_category_abai_category_type_id_wells_well_category_type",
            ),
        ),
        sa.ForeignKeyConstraint(
            ["abai_well_id"],
            ["wells_well.abai_id"],
            name=op.f("fk_wells_well_category_abai_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_category")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_category_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_well_category_abai_category_type_id"),
        "wells_well_category",
        ["abai_category_type_id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_category_abai_well_id_dbeg",
        "wells_well_category",
        ["abai_well_id", "dbeg"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_category_id"),
        "wells_well_category",
        ["id"],
        unique=False,
    )

    op.create_table(
        "wells_geo",
        sa.Column("abai_geo_type_id", sa.BigInteger(), nullable=True),
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("field_code", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_geo_type_id"],
            ["wells_geo_type.abai_id"],
            name=op.f("fk_wells_geo_abai_geo_type_id_wells_geo_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_geo")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_geo_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_geo_abai_geo_type_id"),
        "wells_geo",
        ["abai_geo_type_id"],
        unique=False,
    )
    op.create_index(op.f("ix_wells_geo_id"), "wells_geo", ["id"], unique=False)

    op.create_table(
        "wells_well_geo",
        sa.Column("abai_well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_geo_id", sa.BigInteger(), nullable=False),
        sa.Column("dbeg", sa.Date(), nullable=True),
        sa.Column("dend", sa.Date(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_geo_id"],
            ["wells_geo.abai_id"],
            name=op.f("fk_wells_well_geo_abai_geo_id_wells_geo"),
        ),
        sa.ForeignKeyConstraint(
            ["abai_well_id"],
            ["wells_well.abai_id"],
            name=op.f("fk_wells_well_geo_abai_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_geo")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_geo_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_well_geo_abai_geo_id"),
        "wells_well_geo",
        ["abai_geo_id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_geo_abai_well_id_dbeg",
        "wells_well_geo",
        ["abai_well_id", "dbeg"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_geo_id"),
        "wells_well_geo",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wells_well_geo_id"), table_name="wells_well_geo")
    op.drop_index("ix_wells_well_geo_abai_well_id_dbeg", table_name="wells_well_geo")
    op.drop_index(op.f("ix_wells_well_geo_abai_geo_id"), table_name="wells_well_geo")
    op.drop_table("wells_well_geo")
    op.drop_index(op.f("ix_wells_geo_id"), table_name="wells_geo")
    op.drop_index(op.f("ix_wells_geo_abai_geo_type_id"), table_name="wells_geo")
    op.drop_table("wells_geo")
    op.drop_index(op.f("ix_wells_well_category_id"), table_name="wells_well_category")
    op.drop_index(
        "ix_wells_well_category_abai_well_id_dbeg",
        table_name="wells_well_category",
    )
    op.drop_index(
        op.f("ix_wells_well_category_abai_category_type_id"),
        table_name="wells_well_category",
    )
    op.drop_table("wells_well_category")
    op.drop_index(op.f("ix_wells_geo_type_id"), table_name="wells_geo_type")
    op.drop_table("wells_geo_type")
    op.drop_index(
        op.f("ix_wells_well_category_type_id"),
        table_name="wells_well_category_type",
    )
    op.drop_table("wells_well_category_type")
