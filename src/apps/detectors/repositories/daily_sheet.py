from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.detectors.dto.internal.repositories.daily_sheet import (
    UpdateDailySheetDeliveryDTO,
    UpdateDailySheetDTO,
    UpsertDailySheetDeliveryDTO,
    UpsertDailySheetDTO,
)
from apps.detectors.models.daily_sheet import (
    DetectorDailySheet,
    DetectorDailySheetDelivery,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec

# Что перезаписывается при пересборке; ключ (правило, НГДУ, дата) неизменен.
_MUTABLE_COLUMNS: tuple[str, ...] = (
    "status",
    "file_id",
    "rows_count",
    "coverage",
    "content",
    "built_at",
    "error",
    "config_version",
)


class DetectorDailySheetRepository(
    AsyncAlchemyRepository[
        UpsertDailySheetDTO,
        UpdateDailySheetDTO,
        DetectorDailySheet,
    ],
):
    model = DetectorDailySheet

    async def get(
        self,
        *,
        detector_code: str,
        abai_ngdu_id: int,
        sheet_date: date,
        oil_field_prefixes: str = "",
    ) -> DetectorDailySheet | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    DetectorDailySheet.detector_code == detector_code,
                    DetectorDailySheet.abai_ngdu_id == abai_ngdu_id,
                    DetectorDailySheet.sheet_date == sheet_date,
                    DetectorDailySheet.oil_field_prefixes == oil_field_prefixes,
                ),
            ),
        )

    async def upsert(self, data: UpsertDailySheetDTO) -> DetectorDailySheet:
        """Записать ведомость; повторная сборка за ту же дату перезаписывает.

        Старый файл при этом не удаляется: строка ``files_file`` остаётся, а
        ведомость ссылается на новую — история версий на случай разбора.
        """
        stmt = pg_insert(DetectorDailySheet).values(**data.model_dump())
        stmt = stmt.on_conflict_do_update(
            constraint="uq_detectors_daily_sheet_detector_ngdu_date_fields",
            set_={
                **{name: getattr(stmt.excluded, name) for name in _MUTABLE_COLUMNS},
                "updated_at": func.now(),
            },
        ).returning(DetectorDailySheet)
        result = await self.session.execute(stmt)
        return result.scalar_one()


_DELIVERY_MUTABLE_COLUMNS: tuple[str, ...] = (
    "status",
    "recipients",
    "sheets",
    "subject",
    "sent_at",
    "error",
)


class DetectorDailySheetDeliveryRepository(
    AsyncAlchemyRepository[
        UpsertDailySheetDeliveryDTO,
        UpdateDailySheetDeliveryDTO,
        DetectorDailySheetDelivery,
    ],
):
    model = DetectorDailySheetDelivery

    async def get(
        self,
        *,
        abai_ngdu_id: int,
        sheet_date: date,
    ) -> DetectorDailySheetDelivery | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    DetectorDailySheetDelivery.abai_ngdu_id == abai_ngdu_id,
                    DetectorDailySheetDelivery.sheet_date == sheet_date,
                ),
            ),
        )

    async def upsert(
        self,
        data: UpsertDailySheetDeliveryDTO,
    ) -> DetectorDailySheetDelivery:
        stmt = pg_insert(DetectorDailySheetDelivery).values(**data.model_dump())
        stmt = stmt.on_conflict_do_update(
            constraint="uq_detectors_daily_sheet_delivery_ngdu_date",
            set_={
                **{
                    name: getattr(stmt.excluded, name)
                    for name in _DELIVERY_MUTABLE_COLUMNS
                },
                "updated_at": func.now(),
            },
        ).returning(DetectorDailySheetDelivery)
        result = await self.session.execute(stmt)
        return result.scalar_one()
