"""Утренняя сборка суточных ведомостей R2 / R9 по подключённым НГДУ.

    python -m apps.detectors.tasks.build_daily_sheets.build_daily_sheets
    python -m apps.detectors.tasks.build_daily_sheets.build_daily_sheets \\
        --date 2026-08-31 --detector R9 --ngdu-id 5 --rebuild

Celery ``detectors.daily_sheet.build`` в 07:30 местного за вчерашние сутки —
после суточного прогона R9 (04:10) и ночных загрузок ABAI. Уже собранная
ведомость пропускается, «нет телеметрии за дату» пишется в лог, а остальные
пары НГДУ × правило собираются дальше. Ручной запрос за любую дату — через
``GET /detectors/v1/reports/daily-sheet``, он использует тот же сборщик.
"""

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.dto.queries.daily_sheet import GetDailySheetQuery
from apps.detectors.services.daily_sheet.builder import local_now
from apps.detectors.services.daily_sheet.config import (
    SHEET_DETECTOR_CODES,
    sheet_applies,
)
from apps.detectors.services.daily_sheet.errors import DailySheetDataNotReadyError
from apps.detectors.services.daily_sheet.targets import list_target_ngdus
from apps.detectors.use_cases.get_daily_sheet import GetDailySheetUseCase
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers
from shared.dependencies.db import (
    get_aioboto_client_factory,
    get_aioboto_presign_client_factory,
)

logger = get_logger(__name__)
settings = get_settings()

DAILY_SHEET_TASK = "detectors.daily_sheet.build"


@dataclass(slots=True)
class BuildStats:
    built: int = 0
    cached: int = 0
    skipped: int = 0
    failed: int = 0


class BuildDailySheets:
    def __init__(
        self,
        *,
        sheet_date: date | None = None,
        detector_codes: Sequence[str] = SHEET_DETECTOR_CODES,
        ngdu_id: int | None = None,
        rebuild: bool = False,
    ) -> None:
        self.sheet_date = sheet_date
        self.detector_codes = tuple(detector_codes)
        self.ngdu_id = ngdu_id
        self.rebuild = rebuild

    async def run(self) -> BuildStats:
        sheet_date = self.sheet_date or (local_now().date() - timedelta(days=1))
        stats = BuildStats()
        storage = AiobotoFileStorage(
            bucket_name=settings.S3_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
            presign_client_factory=get_aioboto_presign_client_factory(),
        )
        async with session_makers["app"]() as session:
            # Снимок НГДУ до цикла: rollback на «нет телеметрии» протушил бы ORM.
            orgs = await list_target_ngdus(session, ngdu_id=self.ngdu_id)
            if not orgs:
                logger.warning(
                    "Daily sheets: no NGDU to build for (ngdu_id=%s)",
                    self.ngdu_id,
                )
                return stats
            use_case = GetDailySheetUseCase(session, storage=storage)
            for org in orgs:
                for detector_code in self.detector_codes:
                    if not sheet_applies(detector_code, org.abai_id):
                        continue
                    await self._build_one(
                        use_case,
                        session,
                        stats,
                        detector_code=detector_code,
                        ngdu_id=org.id,
                        sheet_date=sheet_date,
                    )
        logger.info(
            "Daily sheets for %s: built=%s cached=%s skipped=%s failed=%s",
            sheet_date,
            stats.built,
            stats.cached,
            stats.skipped,
            stats.failed,
        )
        return stats

    async def _build_one(  # noqa: PLR0913
        self,
        use_case: GetDailySheetUseCase,
        session: AsyncSession,
        stats: BuildStats,
        *,
        detector_code: str,
        ngdu_id: int,
        sheet_date: date,
    ) -> None:
        query = GetDailySheetQuery(
            detector_code=detector_code,
            ngdu_id=ngdu_id,
            sheet_date=sheet_date,
            rebuild=self.rebuild,
        )
        try:
            sheet = await use_case.execute(query)
        except DailySheetDataNotReadyError as exc:
            await session.rollback()
            stats.skipped += 1
            logger.warning(
                "Daily sheet %s ngdu_id=%s date=%s skipped: %s",
                detector_code,
                ngdu_id,
                sheet_date,
                exc.details,
            )
        except Exception:
            await session.rollback()
            stats.failed += 1
            logger.exception(
                "Daily sheet %s ngdu_id=%s date=%s failed",
                detector_code,
                ngdu_id,
                sheet_date,
            )
        else:
            if sheet.rebuilt:
                stats.built += 1
            else:
                stats.cached += 1


@celery_app.task(name=DAILY_SHEET_TASK)
def build_daily_sheets(sheet_date: str | None = None, *, rebuild: bool = False) -> None:
    run_async(
        BuildDailySheets(
            sheet_date=date.fromisoformat(sheet_date) if sheet_date else None,
            rebuild=rebuild,
        ).run(),
    )


async def main(args: argparse.Namespace) -> None:
    await BuildDailySheets(
        sheet_date=args.date,
        detector_codes=[args.detector] if args.detector else SHEET_DETECTOR_CODES,
        ngdu_id=args.ngdu_id,
        rebuild=args.rebuild,
    ).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build R2/R9 daily sheets.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="YYYY-MM-DD, по умолчанию вчера",
    )
    parser.add_argument("--detector", choices=SHEET_DETECTOR_CODES)
    parser.add_argument(
        "--ngdu-id",
        type=int,
        help="org.id НГДУ; по умолчанию все подключённые",
    )
    parser.add_argument("--rebuild", action="store_true")
    asyncio.run(main(parser.parse_args()))
