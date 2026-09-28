from datetime import date

from pydantic import BaseModel, ConfigDict


class DetectorFindingDTO(BaseModel):
    """Строка суточного среза правила (``detectors_finding``) для чтения.

    ``well_name`` и ``section`` подставляются на чтении: в таблице лежат
    только id скважины и код находки.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    detector_code: str
    fix_date: date
    well_id: int
    well_name: str | None = None
    kind: str
    section: str | None = None
    title: str
    config_version: str
    payload: dict | None
