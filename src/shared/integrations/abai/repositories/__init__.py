from shared.integrations.abai.repositories.brigades import (
    ABAIBrigadeRepository,
)
from shared.integrations.abai.repositories.coord_systems import (
    ABAICoordSystemRepository,
)
from shared.integrations.abai.repositories.gdis import (
    ABAIGdisCurrentRepository,
    ABAIGdisCurrentValueRepository,
    ABAIMetricRepository,
)
from shared.integrations.abai.repositories.orgs import (
    ABAIOrgRepository,
)
from shared.integrations.abai.repositories.repair_work_types import (
    ABAIRepairWorkTypeRepository,
)
from shared.integrations.abai.repositories.spatial_objects import (
    ABAISpatialObjectRepository,
)
from shared.integrations.abai.repositories.tech_mode_prod_oil import (
    ABAITechModeProdOilRepository,
)
from shared.integrations.abai.repositories.well_expl_types import (
    ABAIWellExplTypeRepository,
)
from shared.integrations.abai.repositories.well_expls import (
    ABAIWellExplRepository,
)
from shared.integrations.abai.repositories.well_orgs import (
    ABAIWellOrgRepository,
)
from shared.integrations.abai.repositories.well_statuses import (
    ABAIReasonRepository,
    ABAIWellStatusRepository,
    ABAIWellStatusTypeRepository,
)
from shared.integrations.abai.repositories.well_workovers import (
    ABAIWellWorkoverRepository,
)
from shared.integrations.abai.repositories.wells import ABAIWellRepository

__all__ = (
    "ABAIBrigadeRepository",
    "ABAICoordSystemRepository",
    "ABAIGdisCurrentRepository",
    "ABAIGdisCurrentValueRepository",
    "ABAIMetricRepository",
    "ABAIOrgRepository",
    "ABAIReasonRepository",
    "ABAIRepairWorkTypeRepository",
    "ABAISpatialObjectRepository",
    "ABAITechModeProdOilRepository",
    "ABAIWellExplRepository",
    "ABAIWellExplTypeRepository",
    "ABAIWellOrgRepository",
    "ABAIWellRepository",
    "ABAIWellStatusRepository",
    "ABAIWellStatusTypeRepository",
    "ABAIWellWorkoverRepository",
)
