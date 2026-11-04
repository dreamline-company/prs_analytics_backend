"""Отчёт «Обрыв штанг» (R2) и обводнённость техрежима

Revision ID: 6d5383b26f01
Revises: 10a7a0b68a81
Create Date: 2026-11-05 10:00:00.000000

detectors_r2_loss_report — суточный отчёт: скважины с сигналом R2 без ремонта и
недобор нефти, один на дату по всем НГДУ, xlsx в S3, судьба письма в mail_*.
telemetry_tech_regime.water_cut — обводнённость по режиму из ABAI
(tech_mode_prod_oil.wcut), колонка «Обводнённость по режиму» отчёта.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6d5383b26f01"
down_revision: str | None = "10a7a0b68a81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "telemetry_tech_regime",
        sa.Column("water_cut", sa.Float(), nullable=True),
    )
    op.create_table(
        "detectors_r2_loss_report",
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("wells_count", sa.Integer(), nullable=False),
        sa.Column("total_oil_loss", sa.Float(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("built_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("config_version", sa.String(length=20), nullable=False),
        sa.Column("mail_status", sa.String(length=20), nullable=True),
        sa.Column(
            "mail_recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("mail_sent_at", sa.DateTime(), nullable=True),
        sa.Column("mail_error", sa.Text(), nullable=True),
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
            ["file_id"],
            ["files_file.id"],
            name=op.f("fk_detectors_r2_loss_report_file_id_files_file"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_r2_loss_report")),
        sa.UniqueConstraint(
            "report_date",
            name=op.f("uq_detectors_r2_loss_report_report_date"),
        ),
    )
    op.create_index(
        op.f("ix_detectors_r2_loss_report_id"),
        "detectors_r2_loss_report",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_detectors_r2_loss_report_id"),
        table_name="detectors_r2_loss_report",
    )
    op.drop_table("detectors_r2_loss_report")
    op.drop_column("telemetry_tech_regime", "water_cut")
