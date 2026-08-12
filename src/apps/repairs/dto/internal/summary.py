import datetime

from pydantic import BaseModel, ConfigDict, Field


class UploadParsedSummariesResultDTO(BaseModel):
    """Итог загрузки батча распарсенных сводок."""

    received: int = Field(0, description="Сколько записей пришло в запросе")
    created: int = Field(0, description="Сколько сводок добавлено")
    updated: int = Field(0, description="Сколько существующих сводок перезаписано")
    deduplicated: int = Field(
        0,
        description="Сколько дублей схлопнуто внутри батча",
    )
    skipped_unknown_well: int = Field(
        0,
        description="Сколько записей пропущено из-за неизвестной скважины",
    )
    unknown_wells: list[str] = Field(
        default_factory=list,
        description="Имена скважин, которых нет в справочнике",
    )
    linked_brigades: int = Field(
        0,
        description="Сколько связок «ремонт ↔ бригада» создано",
    )


class RepairSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repair_id: int | None
    well_id: int
    second_well_id: int | None
    date: datetime.date
    brigade_number: int
    pump_type: str
    shift_type_number: int
    car: str
    device_number: str
    shift_details: list[str]
