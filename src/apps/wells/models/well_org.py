from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class WellOrg(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Периоды привязки скважины к орг. объекту (копия emg.well_org).

    ``abai_org_id`` хранит ABAI id организации без FK на ``org``: локальное
    зеркало оргструктуры пропускает объекты без имени или типа, а привязка
    к ним всё равно нужна, чтобы корректно определить текущее подразделение.

    Источник закрывает период правкой ``dend`` у существующей строки, поэтому
    инкрементальная загрузка помимо новых id перечитывает открытые интервалы.
    """

    __tablename__ = "wells_well_org"
    __table_args__ = (
        Index("ix_wells_well_org_abai_well_id_dbeg", "abai_well_id", "dbeg"),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    abai_org_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    dbeg: Mapped[date | None] = mapped_column(Date, nullable=True)
    dend: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
