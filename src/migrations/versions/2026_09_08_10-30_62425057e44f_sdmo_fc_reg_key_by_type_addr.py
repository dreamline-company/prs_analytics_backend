"""sdmo fc_reg: ключ по (type_1900, addr)

Словари регистров в базах SDMO разных НГДУ не совпадают по натуральному id:
один и тот же регистр (type_1900, addr) заведён под разными fc_reg.id, а
один id в разных базах означает разные регистры. Общий справочник
telemetry_sdmo_fc_reg ведётся по паре (type_1900, addr); sdmo_id остаётся как
id в базе, откуда регистр был впервые загружен, и глобально уникальным быть
не обязан.

Revision ID: 62425057e44f
Revises: 431d6233b404
Create Date: 2026-09-08 10:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "62425057e44f"
down_revision: str | Sequence[str] | None = "431d6233b404"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(
        op.f("ix_telemetry_sdmo_fc_reg_sdmo_id"),
        table_name="telemetry_sdmo_fc_reg",
    )
    op.create_index(
        op.f("ix_telemetry_sdmo_fc_reg_sdmo_id"),
        "telemetry_sdmo_fc_reg",
        ["sdmo_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema (возможен, пока sdmo_id ещё уникальны)."""
    op.drop_index(
        op.f("ix_telemetry_sdmo_fc_reg_sdmo_id"),
        table_name="telemetry_sdmo_fc_reg",
    )
    op.create_index(
        op.f("ix_telemetry_sdmo_fc_reg_sdmo_id"),
        "telemetry_sdmo_fc_reg",
        ["sdmo_id"],
        unique=True,
    )
