"""add wells_well_status_type, wells_well_status_reason, wells_well_status

Revision ID: c83f5e1b9a47
Revises: b61e0c4a8d21
Create Date: 2026-09-28 12:10:00.000000

Копия статусов скважин ABAI (emg.well_status) со справочниками статусов и
причин. Время — UTC, как в источнике.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c83f5e1b9a47"
down_revision: str | None = "b61e0c4a8d21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_well_status_type",
        sa.Column("name_ru", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("tbd_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_status_type")),
        sa.UniqueConstraint(
            "abai_id",
            name=op.f("uq_wells_well_status_type_abai_id"),
        ),
    )
    op.create_index(
        op.f("ix_wells_well_status_type_id"),
        "wells_well_status_type",
        ["id"],
        unique=False,
    )
    op.create_table(
        "wells_well_status_reason",
        sa.Column("reason_type", sa.BigInteger(), nullable=False),
        sa.Column("name_ru", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("parent", sa.BigInteger(), nullable=True),
        sa.Column("name_short_ru", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_status_reason")),
        sa.UniqueConstraint(
            "abai_id",
            name=op.f("uq_wells_well_status_reason_abai_id"),
        ),
    )
    op.create_index(
        op.f("ix_wells_well_status_reason_id"),
        "wells_well_status_reason",
        ["id"],
        unique=False,
    )
    op.create_table(
        "wells_well_status",
        sa.Column("abai_well_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.BigInteger(), nullable=True),
        sa.Column("dbeg", sa.DateTime(), nullable=False),
        sa.Column("dend", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_well_id"],
            ["wells_well.abai_id"],
            name=op.f("fk_wells_well_status_abai_well_id_wells_well"),
        ),
        sa.ForeignKeyConstraint(
            ["status"],
            ["wells_well_status_type.abai_id"],
            name=op.f("fk_wells_well_status_status_wells_well_status_type"),
        ),
        sa.ForeignKeyConstraint(
            ["reason"],
            ["wells_well_status_reason.abai_id"],
            name=op.f("fk_wells_well_status_reason_wells_well_status_reason"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_status")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_status_abai_id")),
    )
    op.create_index(
        "ix_wells_well_status_abai_well_id_dbeg",
        "wells_well_status",
        ["abai_well_id", "dbeg"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_status_dend"),
        "wells_well_status",
        ["dend"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_status_id"),
        "wells_well_status",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wells_well_status_id"), table_name="wells_well_status")
    op.drop_index(op.f("ix_wells_well_status_dend"), table_name="wells_well_status")
    op.drop_index(
        "ix_wells_well_status_abai_well_id_dbeg",
        table_name="wells_well_status",
    )
    op.drop_table("wells_well_status")
    op.drop_index(
        op.f("ix_wells_well_status_reason_id"),
        table_name="wells_well_status_reason",
    )
    op.drop_table("wells_well_status_reason")
    op.drop_index(
        op.f("ix_wells_well_status_type_id"),
        table_name="wells_well_status_type",
    )
    op.drop_table("wells_well_status_type")
