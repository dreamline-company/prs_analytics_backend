from sqlalchemy.orm import Mapped

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Brigade(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "org_brigade"

    name_ru: Mapped[str]
    name_ru_short: Mapped[str | None]
    own: Mapped[bool | None]
    org_id: Mapped[int | None]
