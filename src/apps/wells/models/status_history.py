from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class WellStatusHistory(AppBaseModel, IntPkMixin):
    """История статусов скважины: работает / не работает и причина.

    Строки неизменяемые — каждая смена статуса это новая запись, поэтому из
    TimedMixinModel нужен только created_at. Индекс (well_id, created_at)
    обслуживает выборку последнего статуса для карточки скважины.
    """

    __tablename__ = "wells_well_status_history"
    __table_args__ = (
        Index(
            "ix_wells_well_status_history_well_id_created_at",
            "well_id",
            "created_at",
        ),
    )

    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    is_working: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
