from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

# Статусы обработки замера опросчиком.
MEASURE_STATUS_OK = "ok"
MEASURE_STATUS_PARSE_ERROR = "parse_error"


class KbrsMeasure(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Замер СПО из КБРС (Toucan), снятый фоновым опросчиком.

    Одна строка на ``measure_id``. Живые (ещё пишущиеся прибором) замеры
    перечитываются опросчиком, пока их ``end_time`` близок к текущему
    времени; после этого замер считается завершённым и не трогается.
    """

    __tablename__ = "kbrs_measure"

    measure_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    device_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Описание прибора из справочника Toucan (DirectoryData приходит с логином).
    device_description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Размер сырого payload при последнем фетче: если при перечитке размер не
    # изменился — замер не дорос, парсинг и перезаливка не нужны.
    raw_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Номер скважины из паспорта замера (peek по фиксированному смещению).
    well_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MEASURE_STATUS_OK,
    )
    raw_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    chart_json_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    notes_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    passport_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    # Время последнего успешного опроса этого замера.
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
