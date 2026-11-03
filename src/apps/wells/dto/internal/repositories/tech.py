"""Строки зеркала технологических объектов ABAI.

Один DTO на таблицу и для вставки, и для обновления: сверка с источником
всегда пишет строку целиком.
"""

from datetime import date

from shared.dto.repositories import RepositoryDTO


class TechTypeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str | None = None
    name_short_ru: str | None = None
    code: str | None = None


class TechDTO(RepositoryDTO):
    abai_id: int
    abai_tech_type_id: int | None = None


class TechHistoryDTO(RepositoryDTO):
    abai_id: int
    abai_tech_id: int
    abai_parent_id: int | None = None
    name_ru: str | None = None
    name_short_ru: str | None = None
    dbeg: date | None = None
    dend: date | None = None


class WellTechDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    abai_tech_id: int
    tap: int | None = None
    dbeg: date | None = None
    dend: date | None = None
