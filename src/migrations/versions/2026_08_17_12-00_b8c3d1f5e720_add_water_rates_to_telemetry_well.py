"""add qv_water/qm_water to telemetry_well

Revision ID: b8c3d1f5e720
Revises: f2a91c47d8b3
Create Date: 2026-08-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c3d1f5e720"
down_revision: str | None = "f2a91c47d8b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "telemetry_well",
        sa.Column("qv_water", sa.Float(), nullable=True),
    )
    op.add_column(
        "telemetry_well",
        sa.Column("qm_water", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("telemetry_well", "qm_water")
    op.drop_column("telemetry_well", "qv_water")
