from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class NGDU(AppBaseModel, IntPkMixin):
    __tablename__ = "org_ngdu"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
