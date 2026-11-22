"""Журнал уведомлений и общая отметка «просмотрено»

Revision ID: c311ccdd9f75
Revises: a4ff233b1ecc
Create Date: 2026-11-22 23:12:06.433168

notifications_notification — неизменяемый журнал (строки только
дописываются, дубли отсекает уникальный dedup_key). notifications_notification_view
— одно «просмотрено» на уведомление, общее для всех экранов. Времена —
местные наивные, проставляются в Python.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c311ccdd9f75"
down_revision: str | None = "a4ff233b1ecc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "notifications_notification",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_code", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("title_ru", sa.String(length=255), nullable=False),
        sa.Column("body_ru", sa.Text(), nullable=True),
        sa.Column("well_id", sa.BigInteger(), nullable=True),
        sa.Column("abai_ngdu_id", sa.SmallInteger(), nullable=True),
        sa.Column("source_type", sa.String(length=30), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("event_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'alarm')",
            name=op.f("ck_notifications_notification_severity"),
        ),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_notifications_notification_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications_notification")),
        sa.UniqueConstraint(
            "dedup_key",
            name=op.f("uq_notifications_notification_dedup_key"),
        ),
    )
    op.create_index(
        op.f("ix_notifications_notification_created_at"),
        "notifications_notification",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_notification_source",
        "notifications_notification",
        ["source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_notification_well_id_id",
        "notifications_notification",
        ["well_id", "id"],
        unique=False,
    )

    op.create_table(
        "notifications_notification_view",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications_notification.id"],
            name=op.f(
                "fk_notifications_notification_view_notification_id_notifications_notification",
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_notifications_notification_view"),
        ),
        sa.UniqueConstraint(
            "notification_id",
            name=op.f("uq_notifications_notification_view_notification_id"),
        ),
    )
    op.create_index(
        op.f("ix_notifications_notification_view_viewed_at"),
        "notifications_notification_view",
        ["viewed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_notifications_notification_view_viewed_at"),
        table_name="notifications_notification_view",
    )
    op.drop_table("notifications_notification_view")
    op.drop_index(
        "ix_notifications_notification_well_id_id",
        table_name="notifications_notification",
    )
    op.drop_index(
        "ix_notifications_notification_source",
        table_name="notifications_notification",
    )
    op.drop_index(
        op.f("ix_notifications_notification_created_at"),
        table_name="notifications_notification",
    )
    op.drop_table("notifications_notification")
