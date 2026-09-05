"""add wells_well_org

Revision ID: a081af91730e
Revises: a4d9c7e21b58
Create Date: 2026-09-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a081af91730e"
down_revision: str | None = "a4d9c7e21b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_well_org",
        sa.Column("abai_well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_org_id", sa.BigInteger(), nullable=False),
        sa.Column("dbeg", sa.Date(), nullable=True),
        sa.Column("dend", sa.Date(), nullable=True),
        sa.Column("abai_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["abai_well_id"],
            ["wells_well.abai_id"],
            name=op.f("fk_wells_well_org_abai_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_org")),
        sa.UniqueConstraint("abai_id", name=op.f("uq_wells_well_org_abai_id")),
    )
    op.create_index(
        op.f("ix_wells_well_org_abai_org_id"),
        "wells_well_org",
        ["abai_org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_org_dend"),
        "wells_well_org",
        ["dend"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wells_well_org_id"),
        "wells_well_org",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_org_abai_well_id_dbeg",
        "wells_well_org",
        ["abai_well_id", "dbeg"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_wells_well_org_abai_well_id_dbeg", table_name="wells_well_org")
    op.drop_index(op.f("ix_wells_well_org_id"), table_name="wells_well_org")
    op.drop_index(op.f("ix_wells_well_org_dend"), table_name="wells_well_org")
    op.drop_index(op.f("ix_wells_well_org_abai_org_id"), table_name="wells_well_org")
    op.drop_table("wells_well_org")
