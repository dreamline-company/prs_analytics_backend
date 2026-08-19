"""relax nullability of wells_coord and wells_coord_system columns

Revision ID: e5b2c7d84f19
Revises: d1f7b3a95e42
Create Date: 2026-08-19 18:40:00.000000

Копии ABAI-таблиц: в источнике все поля кроме id необязательные, а
пространственный объект хранит либо точку, либо полигон — с NOT NULL на обеих
геометриях загрузка устий была невозможна.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b2c7d84f19"
down_revision: str | None = "d1f7b3a95e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POINT = geoalchemy2.types.Geometry(
    geometry_type="POINT",
    srid=-1,
    spatial_index=False,
    from_text="ST_GeomFromEWKT",
    name="geometry",
)
_POLYGON = geoalchemy2.types.Geometry(
    geometry_type="POLYGON",
    srid=-1,
    spatial_index=False,
    from_text="ST_GeomFromEWKT",
    name="geometry",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("wells_coord_system", "mn", existing_type=sa.TEXT(), nullable=True)
    op.alter_column(
        "wells_coord_system",
        "name_ru",
        existing_type=sa.TEXT(),
        nullable=True,
    )
    op.alter_column(
        "wells_coord_system",
        "srid",
        existing_type=sa.INTEGER(),
        nullable=True,
    )
    op.alter_column(
        "wells_coord",
        "spatial_object_type",
        existing_type=sa.INTEGER(),
        nullable=True,
    )
    op.alter_column(
        "wells_coord",
        "coord_point",
        existing_type=_POINT,
        nullable=True,
    )
    op.alter_column(
        "wells_coord",
        "coord_polygon",
        existing_type=_POLYGON,
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "wells_coord",
        "coord_polygon",
        existing_type=_POLYGON,
        nullable=False,
    )
    op.alter_column(
        "wells_coord",
        "coord_point",
        existing_type=_POINT,
        nullable=False,
    )
    op.alter_column(
        "wells_coord",
        "spatial_object_type",
        existing_type=sa.INTEGER(),
        nullable=False,
    )
    op.alter_column(
        "wells_coord_system",
        "srid",
        existing_type=sa.INTEGER(),
        nullable=False,
    )
    op.alter_column(
        "wells_coord_system",
        "name_ru",
        existing_type=sa.TEXT(),
        nullable=False,
    )
    op.alter_column("wells_coord_system", "mn", existing_type=sa.TEXT(), nullable=False)
