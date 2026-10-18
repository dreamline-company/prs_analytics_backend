from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Double, ForeignKey, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel


class GpsMessage(AppBaseModel):
    """Точка трека машины из «Аргуса» (``get_unit_msgs``).

    Ключ ``(unit_id, time)`` без суррогатного id: он же обслуживает выборки
    трека машины за период и отсекает повторы при перечитке
    (``ON CONFLICT DO NOTHING``). Точки без фикса GPS не хранятся. Сырая
    строка ``params`` трекера не хранится — из неё берётся только зажигание.
    """

    __tablename__ = "gps_message"

    unit_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("gps_unit.id"),
        primary_key=True,
    )
    # Время точки, местное (Атырау), как остальные времена приложения.
    time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    lat: Mapped[float] = mapped_column(Double, nullable=False)
    lon: Mapped[float] = mapped_column(Double, nullable=False)
    speed: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # км/ч
    course: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    height: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # м
    sats: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Зажигание: ign (Вега, УМКа) или io_239 (Teltonika); NULL — трекер его
    # не передаёт.
    ignition: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Когда точка дошла до сервера Аргуса (rcv_tm, местное). Сильно позже
    # time — трекер был без связи и дослал буфер: по этой разнице подбирается
    # окно перечитки.
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
