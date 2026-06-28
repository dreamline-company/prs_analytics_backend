from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class RepairDoc(AppBaseModel, IntPkMixin, TimedMixinModel):  # Акты и ПОР
    __tablename__ = "repairs_repair_docs"

    repair_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id"),
        nullable=True,
        unique=True,
    )

    act_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
    )

    por_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
    )
