from shared.dto.repositories import RepositoryDTO


class CreateCoordDTO(RepositoryDTO):
    abai_id: int
    mn: str
    name_ru: str
    srid: int


class UpdateCoordDTO(RepositoryDTO):
    mn: str | None = None
    name_ru: str | None = None
    srid: int | None = None


class CreateWellCoordDTO(RepositoryDTO):
    abai_id: int
    coords_system_id: int | None = None
    spatial_object_type: int
    coord_point: object | None = None
    coord_polygon: object | None = None


class UpdateWellCoordDTO(RepositoryDTO):
    coords_system_id: int | None = None
    spatial_object_type: int | None = None
    coord_point: object | None = None
    coord_polygon: object | None = None
