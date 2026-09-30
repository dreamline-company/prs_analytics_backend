from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepairDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_id: int
    well_id: int | None
    abai_well_id: int
    repair_type_id: int
    work_list: str | None
    work_plan: str | None
    start_time: datetime
    end_time: datetime | None
    # Ремонт удалён в ABAI — идущим не считается.
    abai_deleted_at: datetime | None = None


class CurrentRepairDTO(RepairDTO):
    """Идущий ремонт скважины — RepairDTO плюс название типа.

    Один ``repair_type_id`` нечитаем, а тип ремонта — то, ради чего этот блок
    и смотрят («Смена насоса», «Ревизия насоса»).
    """

    repair_type_name_ru: str | None = None
