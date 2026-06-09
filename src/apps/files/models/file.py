from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class File(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "files_file"

    file: Mapped[str] = mapped_column(String(500), nullable=False)
