"""wells_well_state + wells_drive_state + wells_state_run

Revision ID: 3a9224dfd503
Revises: 7c7e1ba81e1f
Create Date: 2026-10-28 15:54:00.000000

Итоговое состояние скважины для матрицы и карточки: журнал состояний,
состояние привода по станции СДМО и прогоны таски по НГДУ. Заполняет таска
wells.state.refresh (каждые 5 минут); первый запуск — её CLI. Существующие
таблицы не меняются.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3a9224dfd503"
down_revision: str | None = "7c7e1ba81e1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_well_state",
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_ngdu_id", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=False),
        sa.Column("reason_ru", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_wells_well_state_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_well_state")),
    )
    op.create_index(
        op.f("ix_wells_well_state_id"),
        "wells_well_state",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_wells_well_state_well_id_started_at",
        "wells_well_state",
        ["well_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "uq_wells_well_state_open",
        "wells_well_state",
        ["well_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "wells_drive_state",
        sa.Column("station_id", sa.BigInteger(), nullable=False),
        sa.Column("well_id", sa.BigInteger(), nullable=True),
        sa.Column("running", sa.Boolean(), nullable=True),
        sa.Column("since", sa.DateTime(), nullable=True),
        sa.Column("pending_running", sa.Boolean(), nullable=True),
        sa.Column("pending_since", sa.DateTime(), nullable=True),
        sa.Column("reconfirming", sa.Boolean(), nullable=False),
        sa.Column("last_sample_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["telemetry_sdmo_station.id"],
            name=op.f("fk_wells_drive_state_station_id_telemetry_sdmo_station"),
        ),
        sa.PrimaryKeyConstraint("station_id", name=op.f("pk_wells_drive_state")),
    )

    op.create_table(
        "wells_state_run",
        sa.Column("abai_ngdu_id", sa.SmallInteger(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=False),
        sa.Column("sdmo_outage_since", sa.DateTime(), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("abai_ngdu_id", name=op.f("pk_wells_state_run")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("wells_state_run")
    op.drop_table("wells_drive_state")
    op.drop_index(
        "uq_wells_well_state_open",
        table_name="wells_well_state",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_index(
        "ix_wells_well_state_well_id_started_at",
        table_name="wells_well_state",
    )
    op.drop_index(op.f("ix_wells_well_state_id"), table_name="wells_well_state")
    op.drop_table("wells_well_state")
