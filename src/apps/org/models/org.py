from sqlalchemy.orm import Mapped

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class OrgType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    pass


class Org(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "org"
    parent_id: Mapped[int | None]
    name_ru: Mapped[str]
    name_ru_short: Mapped[str | None]
    org_type_id: Mapped[int]
