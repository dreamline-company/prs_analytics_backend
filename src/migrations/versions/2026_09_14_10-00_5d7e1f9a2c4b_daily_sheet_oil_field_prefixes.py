"""daily sheet oil field prefixes

Фильтр суточной ведомости по месторождениям: ведомость по подмножеству
месторождений НГДУ — отдельный артефакт, ключ расширен колонкой
``oil_field_prefixes`` (отсортированные префиксы имён скважин через запятую,
пустая строка — весь НГДУ). Существующие строки остаются ведомостями по НГДУ.

Revision ID: 5d7e1f9a2c4b
Revises: b3c847399b99
Create Date: 2026-09-14 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d7e1f9a2c4b"
down_revision: str | Sequence[str] | None = "b3c847399b99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "detectors_daily_sheet",
        sa.Column(
            "oil_field_prefixes",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
    )
    op.drop_constraint(
        "uq_detectors_daily_sheet_detector_ngdu_date",
        "detectors_daily_sheet",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_detectors_daily_sheet_detector_ngdu_date_fields",
        "detectors_daily_sheet",
        ["detector_code", "abai_ngdu_id", "sheet_date", "oil_field_prefixes"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM detectors_daily_sheet WHERE oil_field_prefixes <> ''")
    op.drop_constraint(
        "uq_detectors_daily_sheet_detector_ngdu_date_fields",
        "detectors_daily_sheet",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_detectors_daily_sheet_detector_ngdu_date",
        "detectors_daily_sheet",
        ["detector_code", "abai_ngdu_id", "sheet_date"],
    )
    op.drop_column("detectors_daily_sheet", "oil_field_prefixes")
