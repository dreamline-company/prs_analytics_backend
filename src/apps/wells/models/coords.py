from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Coord(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Копия справочника систем координат ABAI (emg.coord_system).

    Все поля кроме abai_id nullable — в источнике они тоже необязательные,
    и терять систему координат из-за пустого имени смысла нет.
    """

    __tablename__ = "wells_coord_system"

    mn: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    srid: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )


class WellCoord(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Копия пространственных объектов ABAI (emg.spatial_object).

    Объект хранит либо точку, либо полигон, поэтому обе геометрии nullable.
    """

    __tablename__ = "wells_coord"

    coords_system_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_coord_system.abai_id"),
    )
    spatial_object_type: Mapped[int | None] = mapped_column(Integer, nullable=True)

    coord_point: Mapped[WKBElement | None] = mapped_column(
        Geometry(
            geometry_type="POINT",
            srid=-1,
            spatial_index=False,
        ),
        nullable=True,
    )

    coord_polygon: Mapped[WKBElement | None] = mapped_column(
        Geometry(
            geometry_type="POLYGON",
            srid=-1,
            spatial_index=False,
        ),
        nullable=True,
    )
