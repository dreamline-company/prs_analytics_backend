"""Перекачка динамограмм, вместо которых сохранилась страница ABAI (ручной запуск).

09–11.09.2026 загрузка шла мимо проверки сессии: протухшая сессия ABAI
отвечала страницей входа (200 HTML), и ~9,7 тыс. таких страниц легли в S3 как
динамограммы. Разбор их отклоняет (``unsupported input: html`` / ``empty``) и
до смены промпта не повторяет.

Для каждой такой динамограммы: вложение скачивается заново (ключ S3 содержит
id вложения ABAI), настоящий файл кладётся по тому же ключу, отклонённый
разбор удаляется — проход по фонду разберёт файл заново. Если ABAI снова
отдаёт не файл, строка не трогается.

    python -m apps.wells.tasks.load_dynamograms.refetch_rejected --dry-run
    python -m apps.wells.tasks.load_dynamograms.refetch_rejected --limit 200
"""

import argparse
import asyncio
from collections import Counter
from io import BytesIO

from sqlalchemy import delete, select

from apps.files.models.file import File
from apps.models_registry import *  # noqa: F403
from apps.repairs.models.analytics import RepairDynamogramAIResult
from apps.repairs.tasks.fill_analytics.ai.dynamogram_analyzer import (
    INPUT_REJECTED_PREFIX,
)
from apps.repairs.tasks.fill_analytics.ai.dynamogram_image import detect_kind
from apps.repairs.tasks.fill_analytics.fetchers.abai_dynamogram_fetcher import (
    REJECTED_KINDS,
)
from apps.wells.models.dynamogram import Dynamogram
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers
from shared.dependencies.db import get_aioboto_client_factory
from shared.integrations.abai.api.client import AbaiAsyncClient

logger = get_logger(__name__)
settings = get_settings()

REJECTED_ERRORS = tuple(f"{INPUT_REJECTED_PREFIX}{kind}" for kind in REJECTED_KINDS)
DEFAULT_CONCURRENCY = 8


def attachment_id(s3_key: str) -> int | None:
    """``dynamograms/{id вложения}/{имя файла}`` → id вложения."""
    parts = s3_key.split("/")
    if len(parts) < 3 or parts[0] != "dynamograms" or not parts[1].isdigit():  # noqa: PLR2004
        return None
    return int(parts[1])


async def _targets(limit: int | None) -> list[tuple[int, str]]:
    stmt = (
        select(Dynamogram.id, File.file)
        .join(
            RepairDynamogramAIResult,
            RepairDynamogramAIResult.dynamogram_id == Dynamogram.id,
        )
        .join(File, File.id == Dynamogram.file_id)
        .where(RepairDynamogramAIResult.error.in_(REJECTED_ERRORS))
        .order_by(Dynamogram.id)
        .limit(limit)
    )
    async with session_makers["app"]() as session:
        return [(row.id, row.file) for row in await session.execute(stmt)]


async def _refetch_one(
    dynamogram_id: int,
    s3_key: str,
    *,
    client: AbaiAsyncClient,
    storage: AiobotoFileStorage,
    semaphore: asyncio.Semaphore,
    dry_run: bool,
) -> str:
    file_id = attachment_id(s3_key)
    if file_id is None:
        return "bad key"
    async with semaphore:
        try:
            payload = await client.fetch_attachment(file_id)
        except Exception:
            logger.exception(
                "Attachment %s (dynamogram %s) failed", file_id, dynamogram_id
            )
            return "failed"
        kind = detect_kind(payload)
        if kind in REJECTED_KINDS:
            return f"still {kind}"
        if dry_run:
            return f"would fix ({kind})"
        await storage.upload_file(BytesIO(payload), s3_key)
        async with session_makers["app"]() as session:
            await session.execute(
                delete(RepairDynamogramAIResult).where(
                    RepairDynamogramAIResult.dynamogram_id == dynamogram_id,
                    RepairDynamogramAIResult.error.in_(REJECTED_ERRORS),
                ),
            )
            await session.commit()
        return f"fixed ({kind})"


async def main(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> Counter:
    targets = await _targets(limit)
    logger.info(
        "Rejected dynamograms to refetch: %s (dry_run=%s)", len(targets), dry_run
    )
    client = AbaiAsyncClient(
        username=settings.ABAI_LOGIN,
        password=settings.ABAI_PASS,
        domain=settings.ABAI_DOMAIN,
        connect_to=settings.ABAI_CONNECT_THROUGH,
        timeout=120,
        max_concurrent_downloads=concurrency,
    )
    storage = AiobotoFileStorage(
        bucket_name=settings.S3_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
    )
    semaphore = asyncio.Semaphore(concurrency)
    try:
        results = await asyncio.gather(
            *(
                _refetch_one(
                    dynamogram_id,
                    s3_key,
                    client=client,
                    storage=storage,
                    semaphore=semaphore,
                    dry_run=dry_run,
                )
                for dynamogram_id, s3_key in targets
            ),
        )
    finally:
        await client.aclose()
    stats = Counter(results)
    logger.info("Refetch rejected dynamograms: %s", dict(stats))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args()
    print(  # noqa: T201
        asyncio.run(
            main(dry_run=args.dry_run, limit=args.limit, concurrency=args.concurrency),
        ),
    )
