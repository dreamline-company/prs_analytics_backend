"""Перенос объектов между бакетами S3/MinIO по префиксу (серверное копирование).

    cd src && python -m shared.database.s3.copy_prefix \\
        --source PRS-DYNAMOGRAM-BUCKET --prefix kbrs/ [--target prs-analytics-bucket]
        [--concurrency 8] [--dry-run]

Разовый шаг перехода на единый бакет приложения: замеры опросчика КБРС лежали
в отдельном бакете, а строки ``files_file`` хранят только ключ — ключи
сохраняются, объекты копируются на стороне сервера без скачивания. Объекты,
уже лежащие в целевом бакете с тем же размером, пропускаются, повторный запуск
безопасен. Целевой бакет по умолчанию — ``S3_BUCKET_NAME``.
"""

import argparse
import asyncio
from collections.abc import AsyncIterator

from botocore.exceptions import ClientError

from core import get_logger
from core.settings import get_settings
from shared.database.s3.interface import AiobotoClient
from shared.dependencies.db import get_aioboto_client_factory

logger = get_logger(__name__)

_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}
_LOG_EVERY = 500


async def iter_objects(
    client: AiobotoClient,
    *,
    bucket: str,
    prefix: str,
) -> AsyncIterator[tuple[str, int]]:
    """(key, size) всех объектов под префиксом; листинг постраничный."""
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = await client.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            if "Key" in obj:
                yield obj["Key"], int(obj.get("Size", 0))
        if not page.get("IsTruncated"):
            return
        token = page.get("NextContinuationToken")


async def _exists_same_size(
    client: AiobotoClient,
    *,
    bucket: str,
    key: str,
    size: int,
) -> bool:
    try:
        head = await client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _MISSING_CODES:
            return False
        raise
    return int(head.get("ContentLength", -1)) == size


async def copy_prefix(
    *,
    source: str,
    target: str,
    prefix: str,
    concurrency: int = 8,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Скопировать объекты; вернуть (copied, skipped, failed)."""
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    done = 0

    async with get_aioboto_client_factory()() as client:
        objects = [
            obj async for obj in iter_objects(client, bucket=source, prefix=prefix)
        ]
        logger.info(
            "Copy %s -> %s prefix=%r: %s objects%s",
            source,
            target,
            prefix,
            len(objects),
            " (dry run)" if dry_run else "",
        )

        async def one(key: str, size: int) -> str:
            nonlocal done
            async with semaphore:
                if await _exists_same_size(client, bucket=target, key=key, size=size):
                    outcome = "skipped"
                else:
                    if not dry_run:
                        await client.copy_object(
                            Bucket=target,
                            Key=key,
                            CopySource={"Bucket": source, "Key": key},
                        )
                    outcome = "copied"
                done += 1
                if done % _LOG_EVERY == 0:
                    logger.info("Copy progress: %s/%s", done, len(objects))
                return outcome

        results = await asyncio.gather(
            *(one(key, size) for key, size in objects),
            return_exceptions=True,
        )

    copied = sum(1 for r in results if r == "copied")
    skipped = sum(1 for r in results if r == "skipped")
    failed = 0
    for (key, _), result in zip(objects, results, strict=True):
        if isinstance(result, BaseException):
            failed += 1
            logger.warning("Copy failed for %s: %r", key, result)
    logger.info(
        "Copy done: copied=%s skipped=%s failed=%s",
        copied,
        skipped,
        failed,
    )
    return copied, skipped, failed


async def main() -> None:
    parser = argparse.ArgumentParser(description="Copy S3 objects between buckets.")
    parser.add_argument("--source", required=True, help="Исходный бакет.")
    parser.add_argument(
        "--target",
        default=None,
        help="Целевой бакет (по умолчанию S3_BUCKET_NAME).",
    )
    parser.add_argument("--prefix", default="", help="Префикс ключей, например kbrs/.")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = args.target or get_settings().S3_BUCKET_NAME
    if args.source == target:
        msg = "Source and target buckets are the same."
        raise SystemExit(msg)
    _, _, failed = await copy_prefix(
        source=args.source,
        target=target,
        prefix=args.prefix,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
