from apps.wells.dto.internal.repositories.tech import (
    TechDTO,
    TechHistoryDTO,
    TechTypeDTO,
    WellTechDTO,
)
from apps.wells.models.tech import Tech, TechHistory, TechType, WellTech
from apps.wells.repositories.abai_mirror import AbaiMirrorRepository


class TechTypeRepository(AbaiMirrorRepository[TechTypeDTO, TechType]):
    model = TechType


class TechRepository(AbaiMirrorRepository[TechDTO, Tech]):
    model = Tech


class TechHistoryRepository(AbaiMirrorRepository[TechHistoryDTO, TechHistory]):
    model = TechHistory


class WellTechRepository(AbaiMirrorRepository[WellTechDTO, WellTech]):
    model = WellTech
