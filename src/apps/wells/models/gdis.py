"""Зеркало ГДИС из ABAI: справочник метрик, исследования скважин и их значения.

Копии ``emg_integration.metric``, ``gdis_current`` и ``gdis_current_value``.
Связи между таблицами — по ABAI id (``abai_id``), как у остальных зеркал:
исследование ссылается на ``wells_well.abai_id``, значение — на исследование
и метрику по их ``abai_id``.
"""

from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class GdisMetric(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Справочник метрик ГДИС (копия emg_integration.metric)."""

    __tablename__ = "wells_gdis_metric"

    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, index=True, nullable=True)
    data_type: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # metric.parent — ABAI id родительской метрики (иерархия справочника). Без
    # FK на саму себя: справочник грузится одним батчем в произвольном порядке.
    parent_abai_id: Mapped[int | None] = mapped_column(
        BigInteger,
        index=True,
        nullable=True,
    )
    # Имя справочника ABAI, откуда берётся value_string у значений метрики.
    dict_table: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_double_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_double_max: Mapped[float | None] = mapped_column(Float, nullable=True)


class GdisCurrent(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Исследование скважины (копия emg_integration.gdis_current).

    В источнике нет отметки изменения, а заключения дописывают позже, поэтому
    загрузчик помимо новых id перечитывает недавние исследования.
    """

    __tablename__ = "wells_gdis_current"
    __table_args__ = (
        # «Последнее исследование скважины» — основной запрос карточки.
        Index(
            "ix_wells_gdis_current_abai_well_id_meas_date",
            "abai_well_id",
            "meas_date",
        ),
    )

    # gdis_current.well — как у техрежима, связь по ABAI id скважины.
    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    meas_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    # Причина и прибор — id справочников ABAI, самих справочников в выгрузке нет.
    reason: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason_txt: Mapped[str | None] = mapped_column(Text, nullable=True)
    device: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_dynamogram: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    conclusion_arr: Mapped[list[int] | None] = mapped_column(
        ARRAY(BigInteger),
        nullable=True,
    )
    conclusion_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class GdisCurrentValue(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Значение метрики исследования (копия emg_integration.gdis_current_value)."""

    __tablename__ = "wells_gdis_current_value"
    __table_args__ = (
        Index(
            "ix_wells_gdis_current_value_gdis_metric",
            "gdis_current_abai_id",
            "metric_abai_id",
        ),
    )

    gdis_current_abai_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_gdis_current.abai_id"),
        nullable=False,
    )
    metric_abai_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_gdis_metric.abai_id"),
        index=True,
        nullable=False,
    )
    value_double: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_string: Mapped[str | None] = mapped_column(Text, nullable=True)
