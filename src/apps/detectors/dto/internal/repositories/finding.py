from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateDetectorFindingDTO(RepositoryDTO):
    detector_code: str
    fix_date: date
    well_id: int
    kind: str
    title: str
    config_version: str
    payload: dict | None = None


class UpdateDetectorFindingDTO(RepositoryDTO):
    title: str | None = None
    payload: dict | None = None
