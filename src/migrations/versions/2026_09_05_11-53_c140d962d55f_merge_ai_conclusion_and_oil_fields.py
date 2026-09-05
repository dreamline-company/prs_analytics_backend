"""merge ai conclusion and oil fields

Revision ID: c140d962d55f
Revises: 4c68e9dce23c, 3645f36503ad
Create Date: 2026-09-05 11:53:00.598856

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c140d962d55f"
down_revision: str | Sequence[str] | None = ("4c68e9dce23c", "3645f36503ad")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
