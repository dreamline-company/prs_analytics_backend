"""gps_unit + gps_message

Revision ID: 7c7e1ba81e1f
Revises: 0915d564e781
Create Date: 2026-10-19 01:00:00.000000

GPS-треки машин из «Аргуса»: справочник машин (таска gps.units.sync, раз в
сутки) и точки трека (gps.tracks.incremental, каждые 15 минут). Ключ точек —
(unit_id, time) без суррогатного id: он же индекс выборки трека за период и
защита от повторов при перечитке.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7c7e1ba81e1f"
down_revision: str | None = "0915d564e781"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gps_unit",
        sa.Column("argus_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("plate_normalized", sa.String(length=32), nullable=True),
        sa.Column("imei", sa.String(length=32), nullable=True),
        sa.Column("hw_type", sa.String(length=64), nullable=True),
        sa.Column("equipment", sa.Text(), nullable=True),
        sa.Column("group_name", sa.Text(), nullable=True),
        sa.Column("vehicle_type", sa.Text(), nullable=True),
        sa.Column("vehicle_model", sa.Text(), nullable=True),
        sa.Column(
            "is_lift",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "tracked",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("cf", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sensors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.Column("track_cursor_at", sa.DateTime(), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gps_unit")),
        sa.UniqueConstraint("argus_id", name=op.f("uq_gps_unit_argus_id")),
    )
    op.create_index(op.f("ix_gps_unit_id"), "gps_unit", ["id"], unique=False)
    op.create_index(
        op.f("ix_gps_unit_plate_normalized"),
        "gps_unit",
        ["plate_normalized"],
        unique=False,
    )

    op.create_table(
        "gps_message",
        sa.Column("unit_id", sa.BigInteger(), nullable=False),
        sa.Column("time", sa.DateTime(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("speed", sa.SmallInteger(), nullable=True),
        sa.Column("course", sa.SmallInteger(), nullable=True),
        sa.Column("height", sa.SmallInteger(), nullable=True),
        sa.Column("sats", sa.SmallInteger(), nullable=True),
        sa.Column("ignition", sa.Boolean(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["gps_unit.id"],
            name=op.f("fk_gps_message_unit_id_gps_unit"),
        ),
        sa.PrimaryKeyConstraint("unit_id", "time", name=op.f("pk_gps_message")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("gps_message")
    op.drop_index(op.f("ix_gps_unit_plate_normalized"), table_name="gps_unit")
    op.drop_index(op.f("ix_gps_unit_id"), table_name="gps_unit")
    op.drop_table("gps_unit")
