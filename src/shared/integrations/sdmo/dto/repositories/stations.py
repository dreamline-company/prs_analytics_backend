from pydantic import ConfigDict

from shared.dto.repositories import RepositoryDTO


class SDMOStationDTO(RepositoryDTO):
    model_config = ConfigDict(from_attributes=True)

    id: int
    place_id: int
    name: str | None = None
    code: str | None = None
    IP: str | None = None
    port: int | None = None
    antena: str | None = None
    sector: str | None = None
    active: bool
    server: int | None = None
    type_1900: int
    info: str | None = None
    local_id: int
    server_id: int
    router: str | None = None
    type_electric: int
    type_router: int | None = None
    fc_1549: str | None = None
    fc_1543: str | None = None
    router_soft_ver: str | None = None
    status: int
    lora: int | None = None
    lora_deveui: str | None = None
    lora_server_port: int | None = None
    network_server_address: str | None = None
    restart_attempts: int | None = None
    serial_number: str | None = None
    check_belt_crash: bool
