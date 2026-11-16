from apps.wells.dto.internal.repositories.category_geo import (
    GeoDTO,
    GeoTypeDTO,
    WellCategoryDTO,
    WellCategoryTypeDTO,
    WellGeoDTO,
)
from apps.wells.models.category_geo import (
    Geo,
    GeoType,
    WellCategory,
    WellCategoryType,
    WellGeo,
)
from apps.wells.repositories.abai_mirror import AbaiMirrorRepository


class WellCategoryTypeRepository(
    AbaiMirrorRepository[WellCategoryTypeDTO, WellCategoryType],
):
    model = WellCategoryType


class WellCategoryRepository(AbaiMirrorRepository[WellCategoryDTO, WellCategory]):
    model = WellCategory


class GeoTypeRepository(AbaiMirrorRepository[GeoTypeDTO, GeoType]):
    model = GeoType


class GeoRepository(AbaiMirrorRepository[GeoDTO, Geo]):
    model = Geo


class WellGeoRepository(AbaiMirrorRepository[WellGeoDTO, WellGeo]):
    model = WellGeo
