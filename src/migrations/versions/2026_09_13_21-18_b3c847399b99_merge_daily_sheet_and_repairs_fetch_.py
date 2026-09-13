"""merge daily sheet and repairs fetch split

Точка слияния двух веток от cd447c125022: e7ea83e376c9 (разделение сбора
данных и аналитики ремонтов) и 9f3c2a7d1e6b (суточная ведомость R2/R9).
Схему не меняет — только сводит головы alembic в одну.

Revision ID: b3c847399b99
Revises: e7ea83e376c9, 9f3c2a7d1e6b
Create Date: 2026-09-13 21:18:53.607878

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b3c847399b99"
down_revision: str | Sequence[str] | None = ("e7ea83e376c9", "9f3c2a7d1e6b")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
