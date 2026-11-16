from apps.wells.repositories.category_geo import (
    GeoRepository,
    GeoTypeRepository,
    WellCategoryRepository,
    WellCategoryTypeRepository,
    WellGeoRepository,
)
from apps.wells.repositories.coords import CoordRepository, WellCoordRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.fund_revision import FundRevisionRepository
from apps.wells.repositories.gdis import (
    GdisCurrentRepository,
    GdisCurrentValueRepository,
    GdisMetricRepository,
)
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.tech import (
    TechHistoryRepository,
    TechRepository,
    TechTypeRepository,
    WellTechRepository,
)
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_expl import (
    WellExplRepository,
    WellExplTypeRepository,
)
from apps.wells.repositories.well_org import WellOrgRepository
from apps.wells.repositories.well_state import (
    WellDriveStateRepository,
    WellStateRepository,
    WellStateRunRepository,
)
from apps.wells.repositories.well_status import (
    WellStatusReasonRepository,
    WellStatusRepository,
    WellStatusTypeRepository,
)

__all__ = (
    "CoordRepository",
    "DynamogramRepository",
    "FundRevisionRepository",
    "GdisCurrentRepository",
    "GdisCurrentValueRepository",
    "GdisMetricRepository",
    "GeoRepository",
    "GeoTypeRepository",
    "SPORepository",
    "TechHistoryRepository",
    "TechRepository",
    "TechTypeRepository",
    "WellCategoryRepository",
    "WellCategoryTypeRepository",
    "WellCoordRepository",
    "WellDriveStateRepository",
    "WellExplRepository",
    "WellExplTypeRepository",
    "WellGeoRepository",
    "WellOrgRepository",
    "WellRepository",
    "WellStateRepository",
    "WellStateRunRepository",
    "WellStatusHistoryRepository",
    "WellStatusReasonRepository",
    "WellStatusRepository",
    "WellStatusTypeRepository",
    "WellTechRepository",
)
