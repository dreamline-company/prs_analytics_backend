import inspect

from apps.files.models.file import File
from apps.org.models.ngdu import NGDU
from apps.repairs.models.docs import RepairDoc
from apps.repairs.models.repair import Repair, RepairType
from apps.repairs.models.reports import RepairSummary
from apps.telemetry.models.tech_regime import TechRegime
from apps.telemetry.models.telemetry import Telemetry
from apps.wells.models.coords import Coord, WellCoord
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.models.spo import SPO
from apps.wells.models.well import Well

__all__ = (
    "NGDU",
    "SPO",
    "Coord",
    "Dynamogram",
    "File",
    "Repair",
    "RepairDoc",
    "RepairSummary",
    "RepairType",
    "TechRegime",
    "Telemetry",
    "Well",
    "WellCoord",
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
