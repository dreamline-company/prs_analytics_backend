"""Разовая загрузка пула доноров.

Пул — расчёт автора в Excel («Пул_доноров_02_09_2026.xlsx», лист «Пул
доноров»), один раз выгруженный в JSON рядом с приложением: ``columns``
связывает ключи с колонками листа. Excel — временный источник; будущий
расчёт будет писать в ту же таблицу доноров.

Повторный запуск перезаписывает доноров по скважине — дублей нет.

    python -m apps.compensation.tasks.import_donor_pool.import_donor_pool
    python -m apps.compensation.tasks.import_donor_pool.import_donor_pool \\
        --file path/to/pool.json
"""

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.dto.internal.repositories.compensation import (
    CreateCompensationDonorDTO,
)
from apps.compensation.repositories import CompensationDonorRepository
from apps.compensation.services.allocation import risk_for_step
from apps.models_registry import *  # noqa: F403
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

DEFAULT_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "donor_pool_2026-09-02.json"
)
NGDU_BY_NAME = {
    "Жайкмунайгаз": AbaiNGDUIDsEnum.ZHMG,
    "Кайнармунайгаз": AbaiNGDUIDsEnum.KMG,
}
SPEED_MARGIN_NOT_CHECKED = "не проверен"


def donor_dto(
    row: dict,
    *,
    well_id: int,
    pool_date: date,
) -> CreateCompensationDonorDTO:
    return CreateCompensationDonorDTO(
        well_id=well_id,
        abai_ngdu_id=NGDU_BY_NAME[row["ngdu"]],
        oil_field_name=row["oil_field"],
        gzu=row["gzu"],
        lift_type=row["lift_type"],
        qn=row["qn"],
        water_cut=row["water_cut"],
        submergence_m=row["submergence_m"],
        speed=row["speed"],
        step_percent=row["step_percent"],
        gain=row["gain"],
        speed_margin_checked=row["speed_margin"] != SPEED_MARGIN_NOT_CHECKED,
        risk=risk_for_step(row["step_percent"]),
        pool_date=pool_date,
        source_row=row,
    )


def read_pool(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def import_pool(session: AsyncSession, data: dict) -> tuple[int, list[str]]:
    """Загрузить пул; вернуть (сколько загружено, скважины, которых у нас нет)."""
    pool_date = date.fromisoformat(data["pool_date"])
    names = [row["well"] for row in data["donors"]]
    wells = {
        well.name: well.id
        for well in await WellRepository(session).list_by_names(names)
    }
    repository = CompensationDonorRepository(session)
    missing = []
    for row in data["donors"]:
        well_id = wells.get(row["well"])
        if well_id is None:
            missing.append(row["well"])
            continue
        await repository.upsert(donor_dto(row, well_id=well_id, pool_date=pool_date))
    await session.commit()
    return len(names) - len(missing), missing


async def main(data: dict) -> None:
    async with session_makers["app"]() as session:
        loaded, missing = await import_pool(session, data)
    logger.info("Donor pool: loaded=%s, missing wells=%s", loaded, missing)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-time donor pool import.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    asyncio.run(main(read_pool(parser.parse_args().file)))
