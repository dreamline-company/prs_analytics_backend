"""Приведение координаты устья к виду, пригодному для карты.

Источник неоднороден: часть скважин лежит в WGS 84 градусами, часть — в
проекционных системах Гаусса-Крюгера, а у части система записана как
``EPSG:4326``, хотя значения проекционные (данные ABAI). Сервис раскладывает
эти случаи и отдаёт либо готовые lat/lon, либо причину, почему точку рисовать
нельзя.
"""

from sqlalchemy import RowMapping

from apps.wells.dto.internal.coord_point import (
    COORD_ISSUE_CRS_MISMATCH,
    COORD_ISSUE_EMPTY,
    COORD_ISSUE_OUT_OF_RANGE,
    COORD_ISSUE_TRANSFORM_FAILED,
    COORD_ISSUE_UNKNOWN_CRS,
    WellCoordPointDTO,
)
from apps.wells.repositories.coords import WellCoordRepository

# Географические системы: значения в градусах.
GEOGRAPHIC_SRIDS = frozenset({4326, 4284})
WGS84_SRID = 4326
MAX_LON = 180.0
MAX_LAT = 90.0

# Что делать со значениями: брать как есть или перепроецировать.
_MODE_AS_IS = "as_is"
_MODE_TRANSFORM = "transform"


class CoordPointService:
    def __init__(self, *, well_coord_repository: WellCoordRepository) -> None:
        self.well_coord_repository = well_coord_repository

    async def resolve(self, coord_abai_id: int | None) -> WellCoordPointDTO | None:
        """Координата скважины для карты; None — если координаты нет вовсе."""
        if coord_abai_id is None:
            return None

        row = await self.well_coord_repository.get_point_by_abai_id(coord_abai_id)
        if row is None or row["x"] is None or row["y"] is None:
            return None

        point = self._raw(row)
        mode, point.issue = self._classify(row["x"], row["y"], row["srid"])
        if mode is None:
            return point

        if mode == _MODE_AS_IS:
            return self._filled(point, lon=row["x"], lat=row["y"])

        transformed = await self.well_coord_repository.transform_to_wgs84(
            x=row["x"],
            y=row["y"],
            srid=row["srid"],
        )
        if transformed is None:
            point.issue = COORD_ISSUE_TRANSFORM_FAILED
            return point

        return self._filled(point, lon=transformed[0], lat=transformed[1])

    @staticmethod
    def _classify(
        x: float,
        y: float,
        srid: int | None,
    ) -> tuple[str | None, str | None]:
        """Возвращает (что делать со значениями, замечание к координате)."""
        if x == 0 and y == 0:
            return None, COORD_ISSUE_EMPTY

        degrees_like = abs(x) <= MAX_LON and abs(y) <= MAX_LAT

        if srid is None:
            # Системы нет: если значения похожи на градусы, рисуем как WGS 84,
            # но помечаем — это допущение, а не факт.
            return (_MODE_AS_IS if degrees_like else None), COORD_ISSUE_UNKNOWN_CRS

        # Географическая система обязана нести градусы, проекционная — метры.
        if (srid in GEOGRAPHIC_SRIDS) != degrees_like:
            return None, COORD_ISSUE_CRS_MISMATCH

        if srid == WGS84_SRID:
            return _MODE_AS_IS, None

        return _MODE_TRANSFORM, None

    @staticmethod
    def _raw(row: RowMapping) -> WellCoordPointDTO:
        return WellCoordPointDTO(
            lat=None,
            lon=None,
            is_mappable=False,
            issue=None,
            x=row["x"],
            y=row["y"],
            srid=row["srid"],
            system=row["system"],
            system_name=row["system_name"],
        )

    @staticmethod
    def _filled(
        point: WellCoordPointDTO,
        *,
        lon: float,
        lat: float,
    ) -> WellCoordPointDTO:
        if abs(lon) > MAX_LON or abs(lat) > MAX_LAT:
            point.issue = COORD_ISSUE_OUT_OF_RANGE
            return point

        point.lon = round(lon, 7)
        point.lat = round(lat, 7)
        point.is_mappable = True
        return point
