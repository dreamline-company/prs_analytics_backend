"""Строки зеркала категорий и геообъектов скважин из ABAI.

Один DTO на таблицу и для вставки, и для обновления: сверка с источником
всегда пишет строку целиком.
"""

from datetime import date, datetime

from shared.dto.repositories import RepositoryDTO


class WellCategoryTypeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str | None = None
    name_short_ru: str | None = None
    code: str | None = None


class WellCategoryDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    abai_category_type_id: int
    dbeg: datetime | None = None
    dend: datetime | None = None


class GeoTypeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str | None = None
    name_short_ru: str | None = None
    code: str | None = None


class GeoDTO(RepositoryDTO):
    abai_id: int
    abai_geo_type_id: int | None = None
    name_ru: str | None = None
    name_short_ru: str | None = None
    field_code: str | None = None


class WellGeoDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    abai_geo_id: int
    dbeg: date | None = None
    dend: date | None = None
