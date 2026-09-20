"""daily sheet delivery journal

Журнал рассылки суточных ведомостей R2/R9 на почту
(detectors_daily_sheet_delivery): одно письмо = (НГДУ, дата) — статус,
получатели, вложенные ведомости, время отправки. Держит идемпотентность
утреннего таска рассылки.

Revision ID: 7a1c3e5b9d2f
Revises: 5d7e1f9a2c4b
Create Date: 2026-09-21 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7a1c3e5b9d2f"
down_revision: str | Sequence[str] | None = "5d7e1f9a2c4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "detectors_daily_sheet_delivery",
        sa.Column("abai_ngdu_id", sa.SmallInteger(), nullable=False),
        sa.Column("sheet_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("recipients", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sheets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_daily_sheet_delivery")),
        sa.UniqueConstraint(
            "abai_ngdu_id",
            "sheet_date",
            name="uq_detectors_daily_sheet_delivery_ngdu_date",
        ),
    )
    op.create_index(
        op.f("ix_detectors_daily_sheet_delivery_id"),
        "detectors_daily_sheet_delivery",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_detectors_daily_sheet_delivery_id"),
        table_name="detectors_daily_sheet_delivery",
    )
    op.drop_table("detectors_daily_sheet_delivery")
