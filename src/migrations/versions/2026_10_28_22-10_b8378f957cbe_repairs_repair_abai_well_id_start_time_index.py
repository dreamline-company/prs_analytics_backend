"""repairs_repair: индекс (abai_well_id, start_time)

Revision ID: b8378f957cbe
Revises: 3a9224dfd503
Create Date: 2026-10-28 22:10:00.000000

Ремонты одной скважины по времени — хронология событий скважины
(GET /wells/v1/wells/{id}/timeline) и МРП. Таблица ~64 тыс. строк, индекс
строится за секунды.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8378f957cbe"
down_revision: str | None = "3a9224dfd503"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_repairs_repair_abai_well_id_start_time",
        "repairs_repair",
        ["abai_well_id", "start_time"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_repairs_repair_abai_well_id_start_time",
        table_name="repairs_repair",
    )
