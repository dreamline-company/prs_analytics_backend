from datetime import date, datetime

from pydantic import ConfigDict

from shared.dto.repositories import RepositoryDTO

# Ключ — код регистра/параметра станции (напр. "2", "5", "279"),
# значение — показание этого регистра за сутки.
RegisterMap = dict[str, int | float]


class SDMOFcDataDayPartedDTO(RepositoryDTO):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_id: int
    data: RegisterMap | None = None
    savetime: datetime
    day: date
