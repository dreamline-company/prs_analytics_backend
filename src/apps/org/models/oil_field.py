from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class OilField(AppBaseModel, IntPkMixin):
    """Месторождение: буквенный префикс имени скважины и НГДУ, к которому оно относится.

    ``prefix`` — часть имени скважины до подчёркивания (``BLG`` в ``BLG_0177``).
    ``ngdu_id`` ссылается на локальный ``org.id`` организации типа НГДУ.
    """

    __tablename__ = "oil_fields"
    __table_args__ = (
        UniqueConstraint("prefix", "ngdu_id", name="uq_oil_fields_prefix_ngdu_id"),
    )

    prefix: Mapped[str] = mapped_column(String(15), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ngdu_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("org.id"),
        nullable=False,
    )
