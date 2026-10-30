"""R10: название детектора без «ЦИТС» — «Снижение дебита по замерам»

Revision ID: 6d04c431ed5c
Revises: ab03e46a75c2
Create Date: 2026-10-30 14:00:00.000000

Название из справочника показывается в матрице, карточке, списке эпизодов и
суточной ведомости; слово «ЦИТС» на экранах убрано по просьбе пользователя.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d04c431ed5c"
down_revision: str | None = "ab03e46a75c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "UPDATE detectors_detector SET name_ru = 'Снижение дебита по замерам' "
        "WHERE code = 'R10'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "UPDATE detectors_detector SET name_ru = 'Снижение дебита по замерам ЦИТС' "
        "WHERE code = 'R10'",
    )
