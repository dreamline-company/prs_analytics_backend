from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Правила, по которым есть ведомость (см. services.daily_sheet.config).
SheetDetectorCode = Literal["R2", "R9", "R10"]


class GetDailySheetQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_code: SheetDetectorCode
    # Локальный org.id НГДУ — тот же ключ, что в /org/v1/ngdus и сводке КПЭ.
    ngdu_id: int = Field(ge=1)
    sheet_date: date
    # Месторождения НГДУ по имени или префиксу из /org/v1/oil-fields (без учёта
    # регистра); None — весь НГДУ.
    oil_field_names: list[str] | None = None
    # Пересобрать, даже если ведомость за дату уже сохранена.
    rebuild: bool = False
    expires_in: int = Field(default=3600, ge=60, le=7 * 24 * 3600)
