"""Ревизия фонда, колонки пула доноров и потери по методике

Revision ID: 10a7a0b68a81
Revises: eb3543da89ed
Create Date: 2026-11-04 10:00:00.000000

wells_fund_revision — строки отчёта «Ревизия фонда скважин» (глубина спуска
насоса, Ндин, способ эксплуатации, даты ПРС). compensation_donor — колонки
ежедневного расчёта пула: в пуле, есть телеметрия, ГЗУ и отвод, время расчёта.
compensation_loss — потери (стоящие скважины) и их величина по факту ABAI.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "10a7a0b68a81"
down_revision: str | None = "eb3543da89ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wells_fund_revision",
        sa.Column("revision_date", sa.Date(), nullable=False),
        sa.Column("well_name", sa.String(length=40), nullable=False),
        sa.Column("well_id", sa.BigInteger(), nullable=True),
        sa.Column("ngdu_name", sa.String(length=100), nullable=True),
        sa.Column("cdng_name", sa.String(length=100), nullable=True),
        sa.Column("lift_type", sa.String(length=30), nullable=True),
        sa.Column("pump_depth_m", sa.Float(), nullable=True),
        sa.Column("hdin_m", sa.Float(), nullable=True),
        sa.Column("gdis_date", sa.Date(), nullable=True),
        sa.Column("last_prs_date", sa.Date(), nullable=True),
        sa.Column("prev_prs_date", sa.Date(), nullable=True),
        sa.Column("plan_oil", sa.Float(), nullable=True),
        sa.Column("fact_oil", sa.Float(), nullable=True),
        sa.Column("fact_liquid", sa.Float(), nullable=True),
        sa.Column("fact_water_cut", sa.Float(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            name=op.f("fk_wells_fund_revision_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wells_fund_revision")),
        sa.UniqueConstraint(
            "revision_date",
            "well_name",
            name="uq_wells_fund_revision_date_well",
        ),
    )
    op.create_index(op.f("ix_wells_fund_revision_id"), "wells_fund_revision", ["id"])
    op.create_index(
        op.f("ix_wells_fund_revision_revision_date"),
        "wells_fund_revision",
        ["revision_date"],
    )
    op.create_index(
        op.f("ix_wells_fund_revision_well_name"),
        "wells_fund_revision",
        ["well_name"],
    )
    op.create_index(
        op.f("ix_wells_fund_revision_well_id"),
        "wells_fund_revision",
        ["well_id"],
    )

    op.add_column(
        "compensation_donor",
        sa.Column(
            "in_pool",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "compensation_donor",
        sa.Column(
            "has_telemetry",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "compensation_donor",
        sa.Column("gzu_abai_id", sa.BigInteger(), nullable=True),
    )
    op.add_column("compensation_donor", sa.Column("tap", sa.Integer(), nullable=True))
    op.add_column(
        "compensation_donor",
        sa.Column("calculated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        op.f("ix_compensation_donor_gzu_abai_id"),
        "compensation_donor",
        ["gzu_abai_id"],
    )

    op.create_table(
        "compensation_loss",
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("abai_ngdu_id", sa.Integer(), nullable=False),
        sa.Column("stop_kind", sa.String(length=10), nullable=False),
        sa.Column("stop_text", sa.Text(), nullable=True),
        sa.Column("stop_since", sa.DateTime(), nullable=False),
        sa.Column("loss", sa.Float(), nullable=True),
        sa.Column("loss_source", sa.String(length=10), nullable=True),
        sa.Column("gzu_abai_id", sa.BigInteger(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
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
            name=op.f("fk_compensation_loss_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compensation_loss")),
        sa.UniqueConstraint("well_id", name=op.f("uq_compensation_loss_well_id")),
    )
    op.create_index(op.f("ix_compensation_loss_id"), "compensation_loss", ["id"])
    op.create_index(
        op.f("ix_compensation_loss_abai_ngdu_id"),
        "compensation_loss",
        ["abai_ngdu_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_compensation_loss_abai_ngdu_id"),
        table_name="compensation_loss",
    )
    op.drop_index(op.f("ix_compensation_loss_id"), table_name="compensation_loss")
    op.drop_table("compensation_loss")
    op.drop_index(
        op.f("ix_compensation_donor_gzu_abai_id"),
        table_name="compensation_donor",
    )
    for column in ("calculated_at", "tap", "gzu_abai_id", "has_telemetry", "in_pool"):
        op.drop_column("compensation_donor", column)
    op.drop_index(
        op.f("ix_wells_fund_revision_well_id"),
        table_name="wells_fund_revision",
    )
    op.drop_index(
        op.f("ix_wells_fund_revision_well_name"),
        table_name="wells_fund_revision",
    )
    op.drop_index(
        op.f("ix_wells_fund_revision_revision_date"),
        table_name="wells_fund_revision",
    )
    op.drop_index(op.f("ix_wells_fund_revision_id"), table_name="wells_fund_revision")
    op.drop_table("wells_fund_revision")
