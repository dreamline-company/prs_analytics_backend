"""Состояние скважины глазами детекторов — свёртка активных эпизодов."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field

from apps.detectors import labels


class ActiveIncidentDTO(BaseModel):
    """Активный эпизод в свёрнутом виде: без улик, только шапка."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    detector_code: str
    detector_name_ru: str | None = None
    reason_code: str
    level: str
    opened_at: datetime
    detected_at: datetime
    last_seen_at: datetime
    escalated_at: datetime | None

    @computed_field
    @property
    def level_ru(self) -> str:
        """У R2 — стадия («Падение нагрузки» / «Возможный обрыв штанг»)."""
        return labels.level_label_ru(self.detector_code, self.level)

    @computed_field
    @property
    def title_ru(self) -> str:
        """Строка сигнала: у R2 — стадия, у прочих — имя правила."""
        return labels.signal_title_ru(
            self.detector_code,
            self.level,
            self.detector_name_ru,
        )


class WellIncidentStatusDTO(BaseModel):
    """Итог по скважине: что показывать в шапке карточки.

    ``level`` — худший уровень среди активных эпизодов (``normal``, если их
    нет). ``title_ru`` — готовая строка для шапки, чтобы фронт не собирал её
    из кодов. ``incidents`` отдаётся целиком: на скважине может висеть
    несколько сигналов разом, и в карточке нужен весь список, а не только
    худший.
    """

    level: str
    title_ru: str
    since: datetime | None
    incidents: list[ActiveIncidentDTO]
