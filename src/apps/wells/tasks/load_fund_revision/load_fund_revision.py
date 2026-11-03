"""Загрузка ревизии фонда скважин в ``wells_fund_revision``.

Сейчас ревизия загружается разово из выгрузки в репозитории (ревизия на
28.10.2026, НГДУ Жайкмунайгаз и Кайнармунайгаз); позже её будет присылать
эндпоинт каждый день — он возьмёт тот же разбор
(``apps.wells.services.fund_revision.parser``). Ревизия на ту же дату
заменяется целиком; скважина сопоставляется с ``wells_well`` по имени.

    python -m apps.wells.tasks.load_fund_revision.load_fund_revision
    python -m apps.wells.tasks.load_fund_revision.load_fund_revision \\
        --file path/to/Ревизия.xlsx --dry-run
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.fund_revision import CreateFundRevisionDTO
from apps.wells.repositories import FundRevisionRepository, WellRepository
from apps.wells.services.fund_revision.parser import (
    FundRevisionRow,
    FundRevisionSheet,
    parse_fund_revision,
)
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

DEFAULT_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "fund_revision_2026-10-28.json"
)


def read_sheet(path: Path) -> FundRevisionSheet:
    """xlsx — разбор отчёта; json — готовая выгрузка (как в репозитории)."""
    if path.suffix.lower() == ".xlsx":
        return parse_fund_revision(path.read_bytes())
    data = json.loads(path.read_text(encoding="utf-8"))
    return FundRevisionSheet(
        revision_date=date.fromisoformat(data["revision_date"]),
        rows=[FundRevisionRow.from_json(row) for row in data["rows"]],
    )


async def load_fund_revision(
    session: AsyncSession,
    sheet: FundRevisionSheet,
    *,
    dry_run: bool = False,
) -> Counter:
    """Записать ревизию; вернуть счётчики (строк, сопоставлено, без скважины)."""
    names = [row.well_name for row in sheet.rows]
    # Без учёта регистра: суффиксы в отчёте и в БД пишут по-разному (VMB_02en).
    wells = {
        well.name.lower(): well.id
        for well in await WellRepository(session).list_by_names_ci(names)
    }
    stats = Counter(rows=len(sheet.rows))
    rows = []
    for row in sheet.rows:
        well_id = wells.get(row.well_name.lower())
        stats["matched" if well_id is not None else "unmatched"] += 1
        rows.append(
            CreateFundRevisionDTO(
                revision_date=sheet.revision_date,
                well_id=well_id,
                **{
                    key: getattr(row, key)
                    for key in FundRevisionRow.__dataclass_fields__
                },
            ),
        )
    if not dry_run:
        await FundRevisionRepository(session).replace_revision(
            sheet.revision_date,
            rows,
        )
    return stats


async def main(path: Path, *, dry_run: bool) -> None:
    sheet = read_sheet(path)
    async with session_makers["app"]() as session:
        stats = await load_fund_revision(session, sheet, dry_run=dry_run)
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    logger.info(
        "Fund revision %s from %s: rows=%s, matched=%s, unmatched=%s%s",
        sheet.revision_date,
        path.name,
        stats["rows"],
        stats["matched"],
        stats["unmatched"],
        " (dry run)" if dry_run else "",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.file, dry_run=args.dry_run))
