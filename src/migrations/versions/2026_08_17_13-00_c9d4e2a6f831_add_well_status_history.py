"""add wells_well_status_history + indexes for well card lookups

Revision ID: c9d4e2a6f831
Revises: b8c3d1f5e720
Create Date: 2026-08-17 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d4e2a6f831"
down_revision: str | None = "b8c3d1f5e720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_well_status_history",
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("is_working", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_wells_well_status_history_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_status_history")),
    )
    op.create_index(
        op.f("ix_wells_well_status_history_id"),
        "wells_well_status_history",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_status_history_well_id_created_at",
        "wells_well_status_history",
        ["well_id", "created_at"],
        unique=False,
    )
    # Карточка скважины берёт последнюю запись по well_id из таблиц без
    # подходящих индексов (1.65M строк в telemetry_well — был seq scan ~100мс).
    op.create_index(
        "ix_telemetry_well_well_id_date_time",
        "telemetry_well",
        ["well_id", "date_time"],
        unique=False,
    )
    op.create_index(
        "ix_telemetry_tech_regime_abai_well_id_start_date",
        "telemetry_tech_regime",
        ["abai_well_id", "start_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_telemetry_tech_regime_abai_well_id_start_date",
        table_name="telemetry_tech_regime",
    )
    op.drop_index(
        "ix_telemetry_well_well_id_date_time",
        table_name="telemetry_well",
    )
    op.drop_index(
        "ix_wells_well_status_history_well_id_created_at",
        table_name="wells_well_status_history",
    )
    op.drop_index(
        op.f("ix_wells_well_status_history_id"),
        table_name="wells_well_status_history",
    )
    op.drop_table("wells_well_status_history")
