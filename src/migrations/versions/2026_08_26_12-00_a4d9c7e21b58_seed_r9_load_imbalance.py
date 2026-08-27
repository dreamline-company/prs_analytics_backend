"""seed R9 (перекос нагрузки, ШГН) в реестр детекторов

Revision ID: a4d9c7e21b58
Revises: f7a3d9c15b28
Create Date: 2026-08-26 12:00:00.000000

Правило суточное: раннер бежит по расписанию раз в сутки, поэтому дебаунс
диспетчера выставлен в сутки — телеметрия приезжает каждые 5 минут, и без него
правило пересчитывало бы одно и то же по сотне раз в день.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d9c7e21b58"
down_revision: str | None = "f7a3d9c15b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "INSERT INTO detectors_detector "
        "(code, name_ru, source, enabled, min_interval_sec) "
        "VALUES ('R9', 'Перекос нагрузки на ходе (ШГН)', 'sdmo', true, 86400)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM detectors_incident WHERE detector_code = 'R9'")
    op.execute("DELETE FROM detectors_cursor WHERE detector_code = 'R9'")
    op.execute("DELETE FROM detectors_detector WHERE code = 'R9'")
