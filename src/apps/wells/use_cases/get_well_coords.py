"""Список скважин с координатами — минимальный набор для отрисовки на карте."""

from apps.wells.dto.internal.well_coords import WellCoordMapPointDTO
from apps.wells.dto.queries.well import GetWellCoordsQuery
from apps.wells.repositories.coords import WellCoordRepository
from apps.wells.services import NGDUWellsService

# Шести знаков хватает на ~0.1 м — точнее карте не нужно, а ответ ощутимо легче.
COORD_PRECISION = 6


class GetWellCoordsUseCase:
    def __init__(
        self,
        *,
        well_coord_repository: WellCoordRepository,
        ngdu_wells_service: NGDUWellsService,
    ) -> None:
        self.well_coord_repository = well_coord_repository
        self.ngdu_wells_service = ngdu_wells_service

    async def execute(
        self,
        query: GetWellCoordsQuery,
    ) -> list[WellCoordMapPointDTO]:
        well_ids: list[int] | None = None
        if query.ngdu_id is not None:
            # Только с фильтром ходим в ABAI за привязкой скважин к НГДУ —
            # без него эндпоинт остаётся полностью локальным.
            wells = await self.ngdu_wells_service.list_wells(query.ngdu_id)
            if not wells:
                return []
            well_ids = [well.id for well in wells]

        rows = await self.well_coord_repository.list_map_points(well_ids)
        return [
            WellCoordMapPointDTO(
                well_id=row["well_id"],
                well_name=row["well_name"],
                lat=round(row["lat"], COORD_PRECISION),
                lon=round(row["lon"], COORD_PRECISION),
            )
            for row in rows
        ]
