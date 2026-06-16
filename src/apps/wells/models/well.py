from sqlalchemy import BigInteger, Boolean, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Well(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "wells_well"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    coords_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_coord.abai_id"),
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
    )
