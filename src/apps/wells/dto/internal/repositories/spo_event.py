from shared.dto.repositories import RepositoryDTO


class CreateSPOEventDTO(RepositoryDTO):
    spo_id: int
    offset: int
    time_text: str | None = None
    code: int | None = None
    text: str
    raw_text: str


class UpdateSPOEventDTO(RepositoryDTO):
    spo_id: int | None = None
    offset: int | None = None
    time_text: str | None = None
    code: int | None = None
    text: str | None = None
    raw_text: str | None = None
