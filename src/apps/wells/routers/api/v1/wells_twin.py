from typing import Annotated

from fastapi import APIRouter, Path

from apps.wells.dto.internal.well_twin import (
    WellTelemetryDTO,
    WellTwinDTO,
    WellTwinStatus,
)
from apps.wells.dto.responses.wells_twin import WellTwinGetResponseDTO
from shared.dto.api import AppResponse

router = APIRouter(prefix="/twin", tags=["wells-twin"])


@router.get("/{well_name}", response_model=AppResponse[WellTwinDTO])
async def get_twin(
    well_name: Annotated[str, Path(title="Well ID")],
) -> WellTwinGetResponseDTO:
    return WellTwinGetResponseDTO(
        data=WellTwinDTO(
            well_name=well_name,
            status=WellTwinStatus(status_name="Остановлен"),
            work_regime="Остановка",
            production_number=15,
            equipment=["ЭЦН-124", "УЭЦЕ-255"],
            telemetry=WellTelemetryDTO(),
        ),
    )
