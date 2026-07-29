from datetime import date, datetime

from pydantic import ConfigDict

from shared.dto.repositories import RepositoryDTO

# Ключ — addr регистра станции (напр. "1991", "1998"), значение — мгновенное
# показание этого регистра в момент savetime. Таблица fc_data_day_parted — это
# внутрисуточный ряд (~1 отсчёт каждые 2 минуты на станцию), а не суточный агрегат.
RegisterMap = dict[str, int | float]


class SDMOFcDataDayPartedDTO(RepositoryDTO):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_id: int
    data: RegisterMap | None = None
    savetime: datetime
    day: date
