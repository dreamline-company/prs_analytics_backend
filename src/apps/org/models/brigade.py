from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.testing.schema import mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Brigade(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "org_brigade"

    name_ru: Mapped[str]
    name_ru_short: Mapped[str | None]
    own: Mapped[bool | None]
    org_id: Mapped[int | None]


class UniqueBrigade(AppBaseModel, IntPkMixin):
    __tablename__ = "org_unique_brigade"
    __table_args__ = (
        UniqueConstraint("name", "ngdu_id", name="uq_org_unique_brigade_name_ngdu"),
    )

    name: Mapped[str]
    ngdu_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("org.id"),
        nullable=False,
    )
