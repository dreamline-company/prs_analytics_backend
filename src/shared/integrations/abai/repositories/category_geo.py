from shared.integrations.abai.models import (
    Geo,
    GeoType,
    WellCategory,
    WellCategoryType,
    WellGeo,
)
from shared.integrations.abai.repositories.base import ABAIKeysetRepository


class ABAIWellCategoryTypeRepository(ABAIKeysetRepository[WellCategoryType]):
    model = WellCategoryType


class ABAIWellCategoryRepository(ABAIKeysetRepository[WellCategory]):
    model = WellCategory


class ABAIGeoTypeRepository(ABAIKeysetRepository[GeoType]):
    model = GeoType


class ABAIGeoRepository(ABAIKeysetRepository[Geo]):
    model = Geo


class ABAIWellGeoRepository(ABAIKeysetRepository[WellGeo]):
    model = WellGeo
