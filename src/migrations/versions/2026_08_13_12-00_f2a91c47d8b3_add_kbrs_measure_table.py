"""add kbrs_measure table

Revision ID: f2a91c47d8b3
Revises: c4f81a7be92d
Create Date: 2026-08-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a91c47d8b3"
down_revision: str | None = "c4f81a7be92d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kbrs_measure",
        sa.Column("measure_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("device_type", sa.Integer(), nullable=False),
        sa.Column("raw_size", sa.BigInteger(), nullable=False),
        sa.Column("well_number", sa.Integer(), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=True),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("raw_file_id", sa.BigInteger(), nullable=True),
        sa.Column("chart_json_file_id", sa.BigInteger(), nullable=True),
        sa.Column("notes_file_id", sa.BigInteger(), nullable=True),
        sa.Column("passport_file_id", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
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
            ["raw_file_id"],
            ["files_file.id"],
            name=op.f("fk_kbrs_measure_raw_file_id_files_file"),
        ),
        sa.ForeignKeyConstraint(
            ["chart_json_file_id"],
            ["files_file.id"],
            name=op.f("fk_kbrs_measure_chart_json_file_id_files_file"),
        ),
        sa.ForeignKeyConstraint(
            ["notes_file_id"],
            ["files_file.id"],
            name=op.f("fk_kbrs_measure_notes_file_id_files_file"),
        ),
        sa.ForeignKeyConstraint(
            ["passport_file_id"],
            ["files_file.id"],
            name=op.f("fk_kbrs_measure_passport_file_id_files_file"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kbrs_measure")),
        sa.UniqueConstraint("measure_id", name=op.f("uq_kbrs_measure_measure_id")),
    )
    op.create_index(op.f("ix_kbrs_measure_id"), "kbrs_measure", ["id"], unique=False)
    op.create_index(
        op.f("ix_kbrs_measure_device_id"),
        "kbrs_measure",
        ["device_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_kbrs_measure_device_id"), table_name="kbrs_measure")
    op.drop_index(op.f("ix_kbrs_measure_id"), table_name="kbrs_measure")
    op.drop_table("kbrs_measure")
