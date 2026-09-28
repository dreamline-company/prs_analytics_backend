from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.daily_sheet import DailySheetDTO
from apps.detectors.dto.queries.daily_sheet import GetDailySheetQuery, SheetDetectorCode
from apps.detectors.dto.responses.daily_sheet import DailySheetResponseDTO
from apps.detectors.use_cases.get_daily_sheet import GetDailySheetUseCase
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.dependencies.db import (
    get_aioboto_client_factory,
    get_aioboto_presign_client_factory,
    get_app_session,
)
from shared.dto.api import AppResponse

router = APIRouter(prefix="/reports", tags=["detectors"])
settings = get_settings()


@router.get(
    "/daily-sheet",
    response_model=AppResponse[DailySheetDTO],
    summary="Суточная ведомость отклонений по правилу R2 / R9 / R10 на НГДУ",
    description=(
        "Ведомость за дату — сохранённый артефакт: первый запрос собирает её "
        "из записанных эпизодов (правило не перезапускается) и кладёт docx в S3, "
        "повторные отдают готовое. 409 — за дату нет телеметрии СДМО по НГДУ "
        "(у R10 — замеров ЦИТС), ведомость не собирается, чтобы пустая таблица "
        "не читалась как «отклонений нет». 404 — правило по этому НГДУ не "
        "считается (R10 — только ЖМГ)."
    ),
)
async def get_daily_sheet(  # noqa: PLR0913
    session: Annotated[AsyncSession, Depends(get_app_session)],
    detector_code: Annotated[SheetDetectorCode, Query(description="R2 | R9 | R10")],
    ngdu_id: Annotated[
        int,
        Query(ge=1, description="НГДУ — org.id из /org/v1/ngdus"),
    ],
    sheet_date: Annotated[
        date,
        Query(alias="date", description="Дата ведомости, YYYY-MM-DD"),
    ],
    oil_field_names: Annotated[
        list[str] | None,
        Query(
            alias="oil_field_name",
            description=(
                "Месторождение по имени или префиксу из /org/v1/oil-fields "
                "(без учёта регистра); параметр повторяемый — "
                "?oil_field_name=BLG&oil_field_name=GRN. Без него — весь НГДУ"
            ),
        ),
    ] = None,
    rebuild: Annotated[  # noqa: FBT002 — query-параметр FastAPI
        bool,
        Query(description="Пересобрать, даже если ведомость за дату уже есть"),
    ] = False,
    expires_in: Annotated[
        int,
        Query(ge=60, le=7 * 24 * 3600, description="Срок ссылки на файл, сек"),
    ] = 3600,
) -> DailySheetResponseDTO:
    storage = AiobotoFileStorage(
        bucket_name=settings.S3_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
        presign_client_factory=get_aioboto_presign_client_factory(),
    )
    sheet = await GetDailySheetUseCase(session, storage=storage).execute(
        GetDailySheetQuery(
            detector_code=detector_code,
            ngdu_id=ngdu_id,
            sheet_date=sheet_date,
            oil_field_names=oil_field_names,
            rebuild=rebuild,
            expires_in=expires_in,
        ),
    )
    return DailySheetResponseDTO(data=sheet)
