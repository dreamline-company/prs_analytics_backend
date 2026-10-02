"""repairs_repair.abai_repair_type_id

Revision ID: 86d46fef817c
Revises: 51838b00c661
Create Date: 2026-10-02 16:00:00.000000

Тип ремонта ABAI (well_workover.repair_type): 1 КРС, 2 ТРС, 3 ПРС, 4 прочие
простои, 5 наземный ремонт. Уже загруженные ремонты заполняет
apps/repairs/tasks/load_repairs/backfill_abai_repair_type.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "86d46fef817c"
down_revision: str | None = "51838b00c661"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "repairs_repair",
        sa.Column(
            "abai_repair_type_id",
            sa.Integer(),
            nullable=True,
            comment=(
                "Тип ремонта ABAI: 1 КРС, 2 ТРС, 3 ПРС, 4 прочие простои, "
                "5 наземный ремонт"
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("repairs_repair", "abai_repair_type_id")
