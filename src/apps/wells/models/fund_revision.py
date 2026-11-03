"""Ревизия фонда скважин — отчёт промысла «Ревизия фонда скважин» на дату.

Отсюда берутся данные, которых нет или мало в ABAI: глубина спуска насоса (у
ЭВН Восточного Молдабека в ABAI её почти нет), способ эксплуатации,
динамический уровень последнего ГДИС, даты двух последних ПРС. Нужны пулу
доноров (методика подбора, ред. 3): погружение = глубина спуска − Ндин.

Каждая ревизия хранится целиком под своей датой; читается последняя.
"""

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class FundRevision(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Строка ревизии: одна скважина на дату ревизии."""

    __tablename__ = "wells_fund_revision"
    __table_args__ = (
        UniqueConstraint(
            "revision_date",
            "well_name",
            name="uq_wells_fund_revision_date_well",
        ),
    )

    revision_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    well_name: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    # Скважина по имени; None — в wells_well такой нет.
    well_id: Mapped[int | None] = mapped_column(
        ForeignKey("wells_well.id"),
        index=True,
        nullable=True,
    )
    ngdu_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cdng_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # ШГН / ЭВН / УЭЦН / Фонтан / …
    lift_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Глубина спуска насоса по способу эксплуатации, м.
    pump_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    hdin_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    gdis_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_prs_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prev_prs_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_oil: Mapped[float | None] = mapped_column(Float, nullable=True)
    fact_oil: Mapped[float | None] = mapped_column(Float, nullable=True)
    fact_liquid: Mapped[float | None] = mapped_column(Float, nullable=True)
    fact_water_cut: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Прочие колонки (СК, привод ЭВН, коды насосов, результат ГДИС) — справочно.
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
