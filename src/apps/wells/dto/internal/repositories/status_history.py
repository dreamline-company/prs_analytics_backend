from shared.dto.repositories import RepositoryDTO


class CreateWellStatusHistoryDTO(RepositoryDTO):
    # created_at не задаётся снаружи — его ставит БД (server_default now()).
    well_id: int
    is_working: bool
    reason: str | None = None


class UpdateWellStatusHistoryDTO(RepositoryDTO):
    is_working: bool | None = None
    reason: str | None = None
