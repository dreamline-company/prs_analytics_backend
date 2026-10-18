from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class GpsUnit(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Машина из справочника «Аргуса» (``get_units``), раз в сутки upsert.

    Машины не удаляются: пропавшая из ответа получает ``removed_at``, на неё
    по-прежнему ссылаются точки трека.
    """

    __tablename__ = "gps_unit"

    # id объекта в Аргусе — его передают в get_unit_msgs (не IMEI).
    argus_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    # Госномер как в Аргусе: "730AU06", "010IZO06 ВО".
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Номер в каноническом виде (apps.gps.services.plates) — для сопоставления
    # с путёвками УТО и сводками ПРС.
    plate_normalized: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    imei: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hw_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # cf_Тип оборудования: Вега MT X CAN / УМКа302 / Teltonika FMB920 …
    equipment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # cf_Имя группы: НГДУ или подрядчик.
    group_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # cf_Тип АТС: "ПОДЪЕМНИК АПРС - 40" …
    vehicle_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # cf_АТС: "УРАЛ 4320-1951-72 АПРС-40" …
    vehicle_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Подъёмник ПРС (АПРС, ПТП, ПАП) — по типу/модели АТС.
    is_lift: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    # Грузить ли трек: подъёмник или машина из путёвок УТО / сводок ПРС.
    # Пересчитывается в начале каждого прогона загрузчика треков.
    tracked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    # Карточка целиком (ключи cf_*), как пришла.
    cf: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Описания датчиков объекта — расшифровка параметров трекера (p28xx …).
    sensors: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    # Время последнего сообщения (lmsg.time) на момент синхронизации.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # До какого момента трек уже запрошен. NULL — трек ещё не грузился:
    # загрузчик начнёт с глубины истории (бэкфилл).
    track_cursor_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Машина пропала из get_units — когда синхронизация это заметила.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
