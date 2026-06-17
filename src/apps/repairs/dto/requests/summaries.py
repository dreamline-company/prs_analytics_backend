from datetime import date
from time import strptime

from pydantic import BaseModel, Field, field_validator


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
