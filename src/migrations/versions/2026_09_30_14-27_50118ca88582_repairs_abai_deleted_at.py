"""repairs_repair.abai_deleted_at

Revision ID: 50118ca88582
Revises: a7f27b3561f0
Create Date: 2026-09-30 14:27:26.924990

Пометка ремонта, пропавшего из ABAI: запись остаётся, идущим не считается.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "50118ca88582"
down_revision: str | None = "a7f27b3561f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "repairs_repair",
        sa.Column("abai_deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("repairs_repair", "abai_deleted_at")
