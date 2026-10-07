"""detectors_verification + detectors_verification_history

Revision ID: 0915d564e781
Revises: 86d46fef817c
Create Date: 2026-10-06 21:53:00.000000

Проверка эпизодов детекции независимыми данными: текущая отметка на эпизод
(ложная / скорее всего поломка / поломка подтверждена / не удалось проверить)
и журнал её смен. Заполняет таска detectors.rod_breaks.verify (ежечасно в :30);
историю за прошлые дни — её CLI с --backfill-days.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0915d564e781"
down_revision: str | None = "86d46fef817c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "detectors_verification",
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("well_id", sa.BigInteger(), nullable=False),
        sa.Column("detector_code", sa.String(length=20), nullable=False),
        sa.Column("verdict", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("evidence_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("final_at", sa.DateTime(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("rule_version", sa.String(length=50), nullable=False),
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
            ["incident_id"],
            ["detectors_incident.id"],
            name=op.f("fk_detectors_verification_incident_id_detectors_incident"),
        ),
        sa.ForeignKeyConstraint(
            ["well_id"],
            ["wells_well.id"],
            name=op.f("fk_detectors_verification_well_id_wells_well"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detectors_verification")),
        sa.UniqueConstraint(
            "incident_id",
            name="uq_detectors_verification_incident",
        ),
    )
    op.create_index(
        op.f("ix_detectors_verification_id"),
        "detectors_verification",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_detectors_verification_well_id",
        "detectors_verification",
        ["well_id"],
        unique=False,
    )
    op.create_index(
        "ix_detectors_verification_not_final",
        "detectors_verification",
        ["incident_id"],
        unique=False,
        postgresql_where=sa.text("is_final = false"),
    )

    op.create_table(
        "detectors_verification_history",
        sa.Column("verification_id", sa.BigInteger(), nullable=False),
        sa.Column("verdict_from", sa.String(length=30), nullable=True),
        sa.Column("verdict_to", sa.String(length=30), nullable=False),
        sa.Column("reason_from", sa.String(length=40), nullable=True),
        sa.Column("reason_to", sa.String(length=40), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("rule_version", sa.String(length=50), nullable=False),
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
            ["verification_id"],
            ["detectors_verification.id"],
            name=op.f(
                "fk_detectors_verification_history_verification_id_"
                "detectors_verification",
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_detectors_verification_history"),
        ),
    )
    op.create_index(
        op.f("ix_detectors_verification_history_id"),
        "detectors_verification_history",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_detectors_verification_history_verification_id",
        "detectors_verification_history",
        ["verification_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_detectors_verification_history_verification_id",
        table_name="detectors_verification_history",
    )
    op.drop_index(
        op.f("ix_detectors_verification_history_id"),
        table_name="detectors_verification_history",
    )
    op.drop_table("detectors_verification_history")
    op.drop_index(
        "ix_detectors_verification_not_final",
        table_name="detectors_verification",
        postgresql_where=sa.text("is_final = false"),
    )
    op.drop_index(
        "ix_detectors_verification_well_id",
        table_name="detectors_verification",
    )
    op.drop_index(
        op.f("ix_detectors_verification_id"),
        table_name="detectors_verification",
    )
    op.drop_table("detectors_verification")
