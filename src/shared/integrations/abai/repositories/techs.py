from shared.integrations.abai.models import Tech, TechHistory, TechType, WellTech
from shared.integrations.abai.repositories.base import ABAIKeysetRepository


class ABAITechTypeRepository(ABAIKeysetRepository[TechType]):
    model = TechType


class ABAITechRepository(ABAIKeysetRepository[Tech]):
    model = Tech


class ABAITechHistoryRepository(ABAIKeysetRepository[TechHistory]):
    model = TechHistory


class ABAIWellTechRepository(ABAIKeysetRepository[WellTech]):
    model = WellTech
