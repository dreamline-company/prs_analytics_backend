"""R10 (события по замерам ЦИТС): реестр детекторов и detectors_finding

Revision ID: d4a7b2c9e815
Revises: c83f5e1b9a47
Create Date: 2026-09-28 12:20:00.000000

Правило суточное: раннер бежит раз в сутки по расписанию, дебаунс диспетчера
— сутки. detectors_finding — суточный срез правила, который не является
эпизодом: «замер устарел», дефекты замеров, сервисный перечень.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4a7b2c9e815"
down_revision: str | None = "c83f5e1b9a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "INSERT INTO detectors_detector "
        "(code, name_ru, source, enabled, min_interval_sec) "
        "VALUES ('R10', 'Снижение дебита по замерам ЦИТС', 'wincc', true, 86400)",
    )
    op.create_table(
        "detectors_finding",
        sa.Column("detector_code", sa.String(length=20), nullable=False),
        sa.Column("fix_date", sa.Date(), nullable=False),
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("config_version", sa.String(length=50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
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
            ["detector_code"],
            ["detectors_detector.code"],
            name=op.f("fk_detectors_finding_detector_code_detectors_detector"),
        ),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_detectors_finding_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_finding")),
        sa.UniqueConstraint(
            "detector_code",
            "fix_date",
            "well_id",
            "kind",
            name="uq_detectors_finding_detector_code_fix_date_well_id_kind",
        ),
    )
    op.create_index(
        "ix_detectors_finding_fix_date",
        "detectors_finding",
        ["fix_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_detectors_finding_id"),
        "detectors_finding",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_detectors_finding_id"), table_name="detectors_finding")
    op.drop_index("ix_detectors_finding_fix_date", table_name="detectors_finding")
    op.drop_table("detectors_finding")
    op.execute("DELETE FROM detectors_incident WHERE detector_code = 'R10'")
    op.execute("DELETE FROM detectors_cursor WHERE detector_code = 'R10'")
    op.execute("DELETE FROM detectors_detector WHERE code = 'R10'")
