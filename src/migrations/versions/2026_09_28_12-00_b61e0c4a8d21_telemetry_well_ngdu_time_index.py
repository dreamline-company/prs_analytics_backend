"""index telemetry_well (abai_ngdu_id, date_time)

Revision ID: b61e0c4a8d21
Revises: 7a1c3e5b9d2f
Create Date: 2026-09-28 12:00:00.000000

Загрузчик WinCC каждый час перечитывает окно НГДУ по времени замера (ключи
уже загруженных строк), R10 выбирает замеры НГДУ за 75 суток.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b61e0c4a8d21"
down_revision: str | None = "7a1c3e5b9d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_telemetry_well_abai_ngdu_id_date_time",
        "telemetry_well",
        ["abai_ngdu_id", "date_time"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_telemetry_well_abai_ngdu_id_date_time",
        table_name="telemetry_well",
    )
