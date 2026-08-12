"""repair summary unique per shift

Сводка ПРС уникальна в разрезе «скважина × сутки × смена»: за одни сутки на
скважине работают две вахты, и каждая сдаёт собственный отчёт. Прежний
UNIQUE(well_id, date) отбрасывал вторую смену и ронял загрузку батча.

Revision ID: c4f81a7be92d
Revises: 61a3284f95d9
Create Date: 2026-08-07 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f81a7be92d"
down_revision: str | None = "61a3284f95d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "repairs_repair_reports"
_OLD_UQ = "uq_repairs_repair_reports_well_id_date"
_NEW_UQ = "uq_repairs_repair_reports_well_id_date_shift"


def upgrade() -> None:
    op.drop_constraint(_OLD_UQ, _TABLE, type_="unique")
    op.create_unique_constraint(
        _NEW_UQ,
        _TABLE,
        ["well_id", "date", "shift_type_number"],
    )


def downgrade() -> None:
    # Обратный переход возможен только если на пару (well_id, date) не набралось
    # больше одной смены — иначе старый constraint не создастся.
    op.execute(
        sa.text(
            f"""
            DELETE FROM {_TABLE} a
            USING {_TABLE} b
            WHERE a.well_id = b.well_id
              AND a.date = b.date
              AND a.id > b.id
            """,
        ),
    )
    op.drop_constraint(_NEW_UQ, _TABLE, type_="unique")
    op.create_unique_constraint(_OLD_UQ, _TABLE, ["well_id", "date"])
