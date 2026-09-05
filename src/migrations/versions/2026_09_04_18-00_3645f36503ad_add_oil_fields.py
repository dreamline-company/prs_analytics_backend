"""add oil_fields

Revision ID: 3645f36503ad
Revises: a081af91730e
Create Date: 2026-09-04 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3645f36503ad"
down_revision: str | None = "a081af91730e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "oil_fields",
        sa.Column("prefix", sa.String(length=15), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ngdu_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["ngdu_id"],
            ["org.id"],
            name=op.f("fk_oil_fields_ngdu_id_org"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oil_fields")),
        sa.UniqueConstraint(
            "prefix",
            "ngdu_id",
            name="uq_oil_fields_prefix_ngdu_id",
        ),
    )
    op.create_index(op.f("ix_oil_fields_id"), "oil_fields", ["id"], unique=False)
    op.create_index(
        op.f("ix_oil_fields_prefix"),
        "oil_fields",
        ["prefix"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_oil_fields_prefix"), table_name="oil_fields")
    op.drop_index(op.f("ix_oil_fields_id"), table_name="oil_fields")
    op.drop_table("oil_fields")
