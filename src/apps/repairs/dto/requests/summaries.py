import re
from datetime import date
from time import strptime

from pydantic import BaseModel, Field, field_validator

# Matches runs of Latin/Cyrillic letters and digits, e.g. "127AZ", "772AT06".
_PLATE_TOKEN_RE = re.compile(r"[A-ZА-Я0-9]{4,10}", re.IGNORECASE)


def _looks_like_plate(token: str) -> bool:
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


class UploadParsedSummaryDTO(BaseModel):
    start_date: date
    brigade_number: int
    well_name: str = Field(..., description="Основаня скважина")
    second_well_name: str | None = Field(None, description="Скважина откуда переехали")
    pump_type: str
    shift_type_number: int
    car: str
    device_number: str
    shift_details: list[str]

    @field_validator("car", mode="before")
    @classmethod
    def extract_car_number(cls, v):
        """Keep only the license plate, dropping the driver name/prefix.

        E.g. "Утеш А. 127AZ" -> "127AZ", "Урал-4320 E964AL" -> "E964AL".
        """
        if not isinstance(v, str):
            return v

        candidates = [t for t in _PLATE_TOKEN_RE.findall(v) if _looks_like_plate(t)]
        return candidates[-1] if candidates else v.strip()

    @field_validator("start_date", mode="before")
    @classmethod
    def validate_start_date(cls, v) -> date:
        if isinstance(v, date):
            return v

        if not isinstance(v, str):
            raise ValueError("Start date must be a string.")

        try:
            parsed = strptime(v, "%d.%m.%Y")
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
        except ValueError as exc:
            raise ValueError("Start date must be in DD.MM.YYYY format.") from exc


class UploadParsedSummariesListDTO(BaseModel):
    summaries: list[UploadParsedSummaryDTO]
