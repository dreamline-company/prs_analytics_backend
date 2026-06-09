from sqlalchemy import BigInteger, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Well(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "wells_well"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    coords_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("wells_coord.abai_id"),
    )
