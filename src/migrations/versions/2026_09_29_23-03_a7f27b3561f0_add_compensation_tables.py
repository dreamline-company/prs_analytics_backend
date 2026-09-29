"""add compensation_donor and compensation_recommendation

Revision ID: a7f27b3561f0
Revises: d4a7b2c9e815
Create Date: 2026-09-29 23:03:00.000000

Контур компенсации: пул доноров (разовая загрузка) и пары «потеря → донор».
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7f27b3561f0"
down_revision: str | None = "d4a7b2c9e815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "compensation_donor",
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_ngdu_id", sa.Integer(), nullable=False),
        sa.Column("oil_field_name", sa.String(length=100), nullable=False),
        sa.Column("gzu", sa.String(length=50), nullable=True),
        sa.Column("lift_type", sa.String(length=10), nullable=False),
        sa.Column("qn", sa.Float(), nullable=False),
        sa.Column("water_cut", sa.Float(), nullable=True),
        sa.Column("submergence_m", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("step_percent", sa.Integer(), nullable=False),
        sa.Column("gain", sa.Float(), nullable=False),
        sa.Column("speed_margin_checked", sa.Boolean(), nullable=False),
        sa.Column("risk", sa.String(length=10), nullable=False),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column(
            "source_row",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_compensation_donor_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compensation_donor")),
        sa.UniqueConstraint("well_id", name=op.f("uq_compensation_donor_well_id")),
    )
    op.create_index(
        op.f("ix_compensation_donor_id"),
        "compensation_donor",
        ["id"],
        unique=False,
    )
    op.create_table(
        "compensation_recommendation",
        sa.Column("loss_well_id", sa.BigInteger(), nullable=False),
        sa.Column("donor_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("loss", sa.Float(), nullable=False),
        sa.Column("gain", sa.Float(), nullable=False),
        sa.Column("speed_from", sa.Float(), nullable=False),
        sa.Column("speed_to", sa.Float(), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("close_reason", sa.String(length=30), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
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
            ["donor_id"],
            ["compensation_donor.id"],
            name=op.f("fk_compensation_recommendation_donor_id_compensation_donor"),
        ),
        sa.ForeignKeyConstraint(
            ["loss_well_id"],
            ["wells_well.id"],
            name=op.f("fk_compensation_recommendation_loss_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compensation_recommendation")),
    )
    op.create_index(
        op.f("ix_compensation_recommendation_id"),
        "compensation_recommendation",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_compensation_recommendation_loss_well_id",
        "compensation_recommendation",
        ["loss_well_id"],
        unique=False,
    )
    op.create_index(
        "uq_compensation_recommendation_open_donor",
        "compensation_recommendation",
        ["donor_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_compensation_recommendation_open_donor",
        table_name="compensation_recommendation",
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.drop_index(
        "ix_compensation_recommendation_loss_well_id",
        table_name="compensation_recommendation",
    )
    op.drop_index(
        op.f("ix_compensation_recommendation_id"),
        table_name="compensation_recommendation",
    )
    op.drop_table("compensation_recommendation")
    op.drop_index(op.f("ix_compensation_donor_id"), table_name="compensation_donor")
    op.drop_table("compensation_donor")
