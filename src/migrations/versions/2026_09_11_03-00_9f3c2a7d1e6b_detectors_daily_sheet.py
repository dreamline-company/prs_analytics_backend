"""detectors daily sheet

Суточные ведомости отклонений R2/R9 по НГДУ (detectors_daily_sheet): одна
строка на (правило, НГДУ, дата) — статус сборки, файл docx в S3, охват суток
и содержимое строк в JSONB. Правило при сборке не перезапускается.

Revision ID: 9f3c2a7d1e6b
Revises: cd447c125022
Create Date: 2026-09-11 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9f3c2a7d1e6b"
down_revision: str | Sequence[str] | None = "cd447c125022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "detectors_daily_sheet",
        sa.Column("detector_code", sa.String(length=20), nullable=False),
        sa.Column("abai_ngdu_id", sa.SmallInteger(), nullable=False),
        sa.Column("sheet_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("rows_count", sa.Integer(), nullable=False),
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("built_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("config_version", sa.String(length=20), nullable=False),
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
            name=op.f("fk_detectors_daily_sheet_detector_code_detectors_detector"),
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files_file.id"],
            name=op.f("fk_detectors_daily_sheet_file_id_files_file"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_daily_sheet")),
        sa.UniqueConstraint(
            "detector_code",
            "abai_ngdu_id",
            "sheet_date",
            name="uq_detectors_daily_sheet_detector_ngdu_date",
        ),
    )
    op.create_index(
        op.f("ix_detectors_daily_sheet_id"),
        "detectors_daily_sheet",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_detectors_daily_sheet_id"), table_name="detectors_daily_sheet",
    )
    op.drop_table("detectors_daily_sheet")
