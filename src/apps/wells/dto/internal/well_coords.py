from pydantic import BaseModel

# Источник статуса пока не определён — отдаём константу, чтобы контракт поля
# уже был на месте и фронт мог раскрашивать точки.
DEFAULT_WELL_STATUS = 1


class WellCoordMapPointDTO(BaseModel):
    """Точка скважины для карты — только то, что нужно её отрисовать."""

    well_id: int
    well_name: str
    lat: float
    lon: float
    well_status: int = DEFAULT_WELL_STATUS
