from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class SPOEvent(AppBaseModel, IntPkMixin):
    __tablename__ = "repairs_spo_event"

    spo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_spo.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    time_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
