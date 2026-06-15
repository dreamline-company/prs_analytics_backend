from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import BigInteger, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Coord(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "wells_coord_system"

    mn: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    srid: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )


class WellCoord(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "wells_coord"

    coords_system_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_coord_system.abai_id"),
    )
    spatial_object_type: Mapped[int] = mapped_column(Integer, nullable=False)

    coord_point: Mapped[WKBElement | None] = mapped_column(
        Geometry("POINT"),
        nullable=True,
    )

    coord_polygon: Mapped[WKBElement | None] = mapped_column(
        Geometry("POLYGON"),
        nullable=True,
    )
