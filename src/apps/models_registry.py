import inspect

from apps.compensation.models.compensation import (
    CompensationDonor,
    CompensationRecommendation,
)
from apps.detectors.models.conclusion import (
    DetectorConclusion,
    DetectorConclusionFeedback,
)
from apps.detectors.models.daily_sheet import (
    DetectorDailySheet,
    DetectorDailySheetDelivery,
)
from apps.detectors.models.finding import DetectorFinding
from apps.detectors.models.incident import (
    Detector,
    DetectorCursor,
    DetectorIncident,
)
from apps.detectors.models.verification import (
    DetectorVerification,
    DetectorVerificationHistory,
)
from apps.files.models.file import File
from apps.kbrs.models.measure import KbrsMeasure
from apps.org.models.brigade import Brigade, UniqueBrigade
from apps.org.models.ngdu import NGDU
from apps.org.models.oil_field import OilField
from apps.org.models.org import Org, OrgType
from apps.repairs.models.analytics import (
    RepairAIAnalysis,
    RepairAnalytics,
    RepairAnalyticsBrigadeErrorScreen,
    RepairAnalyticsDynamogram,
    RepairAnalyticsSPO,
    RepairDynamogramAIResult,
    RepairKPI,
    RepairSPOAIResult,
)
from apps.repairs.models.brigade import RepairBrigade
from apps.repairs.models.docs import RepairDoc
from apps.repairs.models.repair import Repair, RepairType
from apps.repairs.models.reports import RepairSummary
from apps.repairs.models.transport import RepairTransport
from apps.telemetry.models.sdmo import SdmoFcData, SdmoFcReg, SdmoStation
from apps.telemetry.models.tech_regime import TechRegime
from apps.telemetry.models.telemetry import Telemetry
from apps.wells.models.coords import Coord, WellCoord
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.models.gdis import GdisCurrent, GdisCurrentValue, GdisMetric
from apps.wells.models.spo import SPO
from apps.wells.models.spo_event import SPOEvent
from apps.wells.models.status_history import WellStatusHistory
from apps.wells.models.well import Well
from apps.wells.models.well_expl import WellExpl, WellExplType
from apps.wells.models.well_org import WellOrg
from apps.wells.models.well_status import WellStatus, WellStatusReason, WellStatusType

__all__ = (
    "NGDU",
    "SPO",
    "Brigade",
    "CompensationDonor",
    "CompensationRecommendation",
    "Coord",
    "Detector",
    "DetectorConclusion",
    "DetectorConclusionFeedback",
    "DetectorCursor",
    "DetectorDailySheet",
    "DetectorDailySheetDelivery",
    "DetectorFinding",
    "DetectorIncident",
    "DetectorVerification",
    "DetectorVerificationHistory",
    "Dynamogram",
    "File",
    "GdisCurrent",
    "GdisCurrentValue",
    "GdisMetric",
    "KbrsMeasure",
    "OilField",
    "Org",
    "OrgType",
    "Repair",
    "RepairAIAnalysis",
    "RepairAnalytics",
    "RepairAnalyticsBrigadeErrorScreen",
    "RepairAnalyticsDynamogram",
    "RepairAnalyticsSPO",
    "RepairBrigade",
    "RepairDoc",
    "RepairDynamogramAIResult",
    "RepairKPI",
    "RepairSPOAIResult",
    "RepairSummary",
    "RepairTransport",
    "RepairType",
    "SPOEvent",
    "SdmoFcData",
    "SdmoFcReg",
    "SdmoStation",
    "TechRegime",
    "Telemetry",
    "UniqueBrigade",
    "Well",
    "WellCoord",
    "WellExpl",
    "WellExplType",
    "WellOrg",
    "WellStatus",
    "WellStatusHistory",
    "WellStatusReason",
    "WellStatusType",
)

from shared.database.sql.models import AppBaseModel


def validate_model_exports() -> None:
    errors: list[str] = []

    for model_name in __all__:
        model = globals().get(model_name)

        if model is None:
            errors.append(f"{model_name}: object not found in globals()")
            continue

        if not inspect.isclass(model):
            errors.append(f"{model_name}: is not a class, is a {type(model)!r}")
            continue

        if model is AppBaseModel:
            continue

        if not issubclass(model, AppBaseModel):
            errors.append(
                f"{model_name}: is not {AppBaseModel.__name__} or "
                "not its subclass. All migrating models must subclass of "
                "AppBaseModel.",
            )

    if errors:
        message = "Export errors:\n" + "\n".join(f"- {error}" for error in errors)
        raise TypeError(message)


validate_model_exports()
