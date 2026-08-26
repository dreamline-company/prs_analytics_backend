"""detectors core: detector registry, incidents, cursors; drop rod_break tables

Revision ID: f7a3d9c15b28
Revises: e5b2c7d84f19
Create Date: 2026-08-24 16:00:00.000000

Подсистема детекции нарушений: события (эпизоды warning/alarm с нормализацией)
и позиции чтения потоков. Старые таблицы rod_break (журнал прогонов + строка на
скважину за прогон) пусты и заменяются этой схемой.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "f7a3d9c15b28"
down_revision: str | None = "e5b2c7d84f19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "detectors_detector",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name_ru", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("min_interval_sec", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_detector")),
        sa.UniqueConstraint("code", name=op.f("uq_detectors_detector_code")),
    )
    op.create_index(
        op.f("ix_detectors_detector_id"),
        "detectors_detector",
        ["id"],
        unique=False,
    )

    op.create_table(
        "detectors_incident",
        sa.Column("detector_code", sa.String(length=20), nullable=False),
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("reason_code", sa.String(length=30), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("normalized_at", sa.DateTime(), nullable=True),
        sa.Column("close_reason", sa.String(length=20), nullable=True),
        sa.Column("config_version", sa.String(length=50), nullable=False),
        sa.Column("payload", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["detector_code"],
            ["detectors_detector.code"],
            name=op.f("fk_detectors_incident_detector_code_detectors_detector"),
        ),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_detectors_incident_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_incident")),
    )
    op.create_index(
        op.f("ix_detectors_incident_id"),
        "detectors_incident",
        ["id"],
        unique=False,
    )
    op.create_index(
        "uq_detectors_incident_active",
        "detectors_incident",
        ["detector_code", "well_id", "reason_code"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_detectors_incident_well_id_opened_at",
        "detectors_incident",
        ["well_id", "opened_at"],
        unique=False,
    )
    op.create_index(
        "ix_detectors_incident_opened_at",
        "detectors_incident",
        ["opened_at"],
        unique=False,
    )

    op.create_table(
        "detectors_cursor",
        sa.Column("detector_code", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["detector_code"],
            ["detectors_detector.code"],
            name=op.f("fk_detectors_cursor_detector_code_detectors_detector"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_cursor")),
        sa.UniqueConstraint(
            "detector_code",
            "entity_id",
            name="uq_detectors_cursor_detector_code_entity_id",
        ),
    )
    op.create_index(
        op.f("ix_detectors_cursor_id"),
        "detectors_cursor",
        ["id"],
        unique=False,
    )

    # Сид реестра: единственное боевое правило.
    op.execute(
        "INSERT INTO detectors_detector "
        "(code, name_ru, source, enabled, min_interval_sec) "
        "VALUES ('R2', 'Обрыв штанги', 'sdmo', true, 0)",
    )

    # Старые таблицы rod_break пусты — данные не переносятся.
    op.drop_table("detector_rod_break_detection")
    op.drop_table("detector_rod_break_run")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "detector_rod_break_run",
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("wells_scanned", sa.Integer(), nullable=False),
        sa.Column("detections_count", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detector_rod_break_run")),
    )
    op.create_index(
        op.f("ix_detector_rod_break_run_id"),
        "detector_rod_break_run",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_detector_rod_break_run_as_of_date"),
        "detector_rod_break_run",
        ["as_of_date"],
        unique=False,
    )
    op.create_table(
        "detector_rod_break_detection",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("station_sdmo_id", sa.Integer(), nullable=True),
        sa.Column("detector_code", sa.String(length=10), nullable=False),
        sa.Column("fired", sa.Boolean(), nullable=False),
        sa.Column("fired_at", sa.DateTime(), nullable=True),
        sa.Column("failure_dt", sa.DateTime(), nullable=True),
        sa.Column("lead_time_hours", sa.Float(), nullable=True),
        sa.Column("event_class", sa.String(length=30), nullable=True),
        sa.Column("base_moment", sa.Float(), nullable=True),
        sa.Column("low_confidence", sa.Boolean(), nullable=False),
        sa.Column("evidence", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["detector_rod_break_run.id"],
            name=op.f("fk_detector_rod_break_detection_run_id_detector_rod_break_run"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_detector_rod_break_detection_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detector_rod_break_detection")),
    )
    op.create_index(
        op.f("ix_detector_rod_break_detection_id"),
        "detector_rod_break_detection",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_detector_rod_break_detection_run_id"),
        "detector_rod_break_detection",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_detector_rod_break_detection_well_id"),
        "detector_rod_break_detection",
        ["well_id"],
        unique=False,
    )

    op.drop_index(op.f("ix_detectors_cursor_id"), table_name="detectors_cursor")
    op.drop_table("detectors_cursor")
    op.drop_index("ix_detectors_incident_opened_at", table_name="detectors_incident")
    op.drop_index(
        "ix_detectors_incident_well_id_opened_at",
        table_name="detectors_incident",
    )
    op.drop_index("uq_detectors_incident_active", table_name="detectors_incident")
    op.drop_index(op.f("ix_detectors_incident_id"), table_name="detectors_incident")
    op.drop_table("detectors_incident")
    op.drop_index(op.f("ix_detectors_detector_id"), table_name="detectors_detector")
    op.drop_table("detectors_detector")
