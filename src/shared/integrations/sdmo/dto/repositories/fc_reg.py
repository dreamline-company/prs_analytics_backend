from pydantic import ConfigDict

from shared.dto.repositories import RepositoryDTO


class SDMOFcRegDTO(RepositoryDTO):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type_1900: int | None = None
    addr: int
    name: str
    units: str | None = None
    koef: float | None = None
    type: str | None = None
    dynamic: bool | None = None
    info: str
    lora_bytes_size: int | None = None
