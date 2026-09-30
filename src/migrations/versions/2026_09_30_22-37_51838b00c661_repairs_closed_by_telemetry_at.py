"""repairs_repair.closed_by_telemetry_at

Revision ID: 51838b00c661
Revises: 50118ca88582
Create Date: 2026-09-30 22:37:00.000000

Пометка ремонта, закрытого на нашей стороне по телеметрии (скважина уже
работает, а в ABAI ремонт ещё открыт).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "51838b00c661"
down_revision: str | None = "50118ca88582"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "repairs_repair",
        sa.Column("closed_by_telemetry_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("repairs_repair", "closed_by_telemetry_at")
