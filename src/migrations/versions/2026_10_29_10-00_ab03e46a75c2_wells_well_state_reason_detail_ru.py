"""wells_well_state.reason_detail_ru

Revision ID: ab03e46a75c2
Revises: b8378f957cbe
Create Date: 2026-10-29 10:00:00.000000

reason_ru — текст для оператора без источников («Остановлена: Нет подачи»),
reason_detail_ru — тот же вывод с источниками («Привод остановлен (СДМО), в
ABAI «Нет подачи»»). Открытые строки заполнит первый прогон wells.state.refresh
после выката (смена текста при том же состоянии переписывает строку на месте);
у закрытых строк колонка остаётся NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab03e46a75c2"
down_revision: str | None = "b8378f957cbe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "wells_well_state",
        sa.Column("reason_detail_ru", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("wells_well_state", "reason_detail_ru")
