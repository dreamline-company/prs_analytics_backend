from pydantic import BaseModel


class WellMatrixIncidentWellDTO(BaseModel):
    id: int
    well_name: str


class WellMatrixIncidentExplDTO(BaseModel):
    """Способ эксплуатации по последнему периоду скважины."""

    name_ru: str | None


class WellMatrixIncidentDTO(BaseModel):
    well: WellMatrixIncidentWellDTO
    expl: WellMatrixIncidentExplDTO | None = None
