"""Пул доноров по методике (ред. 3) — раз в сутки в 06:10.

Работающий фонд Жайкмунайгаза и VMB проверяется критериями
``apps.compensation.services.pool``. Прошедшие пишутся в
``compensation_donor``: с телеметрией — в пул (``in_pool``), без неё — отдельный
перечень (``has_telemetry = false``, в подбор не идут). Доноры, которые
больше не проходят или не работают, остаются строками с ``in_pool = false``:
на них ссылаются пары, а открытые пары закроет подбор (донор недоступен).

    python -m apps.compensation.tasks.build_pool.build_pool [--dry-run]
"""

import argparse
import asyncio
import math
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select, text

from apps.celery_app import celery_app, run_async
from apps.compensation.dto.internal.repositories.compensation import (
    CreateCompensationDonorDTO,
)
from apps.compensation.models.compensation import CompensationDonor
from apps.compensation.repositories import CompensationDonorRepository
from apps.compensation.services.allocation import risk_for_step
from apps.compensation.services.pool import (
    CRITERIA,
    evaluate,
    field_norms,
    funnel,
)
from apps.compensation.services.pool_data import load_pool_data
from apps.models_registry import *  # noqa: F403
from apps.org.models.oil_field import OilField
from core import get_logger
from core.settings import get_settings
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

METHOD = "Методика подбора скважин-доноров, ред. 3"
# Защита от сбоя данных (пустая таблица состояний, долгий сбой СДМО): если из
# пула прошлого расчёта выпадает больше этой доли — не записывать, а сообщить.
# Строки Excel-пула (без calculated_at) в базу не входят.
MAX_DROP_SHARE = 0.5
MIN_GUARDED_POOL = 10
NGDU_TITLES = {
    int(AbaiNGDUIDsEnum.ZHMG): "Жайык",
    int(AbaiNGDUIDsEnum.KMG): "Кайнар VMB",
}


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _clean(value: object) -> object:
    """NaN и бесконечность — None: PostgreSQL не примет их в JSONB."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def drop_guard(previous: set[int], still_in_pool: set[int]) -> str | None:
    """Почему не записывать расчёт: из пула выпадает слишком много."""
    if len(previous) < MIN_GUARDED_POOL:
        return None
    dropped = len(previous - still_in_pool)
    if dropped > len(previous) * MAX_DROP_SHARE:
        return f"из пула выпадает {dropped} из {len(previous)} доноров"
    return None


async def build_pool(
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
    async with session_makers["app"]() as app, session_makers["abai"]() as abai:
        await abai.execute(text("SET TRANSACTION READ ONLY"))
        if dry_run:
            await app.execute(text("SET TRANSACTION READ ONLY"))
        data = await load_pool_data(app, abai, now)
        norms = field_norms(data.inputs.values())
        results = {
            well_id: evaluate(item, norms) for well_id, item in data.inputs.items()
        }
        field_names = dict(
            (await app.execute(select(OilField.prefix, OilField.name))).all(),
        )
        existing = {
            well_id: (in_pool, calculated_at)
            for well_id, in_pool, calculated_at in await app.execute(
                select(
                    CompensationDonor.well_id,
                    CompensationDonor.in_pool,
                    CompensationDonor.calculated_at,
                ),
            )
        }

        rows, out_of_pool = [], {}
        for well in data.wells:
            item, result = data.inputs[well.id], results[well.id]
            tel = item.telemetry
            source_row = {
                "method": METHOD,
                "calculated_at": now.isoformat(timespec="minutes"),
                "fails": [text_ for _key, text_ in result.fails],
                "flags": result.flags,
                "oil": _round(item.oil),
                "cv": _round(item.cv),
                "water_cut": _round(item.water_cut, 1),
                "days": item.days,
                "pump_depth_m": item.pump_depth,
                "hdin_m": item.hdin,
                "submergence_m": _round(result.submergence, 1),
                "mrp_days": item.mrp_days,
                "pump_age_days": item.pump_age_days,
                "telemetry": (
                    {
                        "samples": tel.samples,
                        "k": _round(tel.k),
                        "moment_cv": _round(tel.moment_cv, 4),
                        "moment_drift": _round(tel.moment_drift, 4),
                        "fill_p5": _round(tel.fill_p5, 1),
                        "speed": _round(tel.speed, 1),
                    }
                    if tel is not None
                    else None
                ),
                **data.details[well.id],
            }
            source_row = _clean(source_row)
            if not result.passed:
                if existing.get(well.id, (False, None))[0]:
                    out_of_pool[well.id] = source_row
                continue
            gzu = data.gzu.get(well.id)
            rows.append(
                CreateCompensationDonorDTO(
                    well_id=well.id,
                    abai_ngdu_id=well.abai_ngdu_id,
                    oil_field_name=field_names.get(item.field, item.field),
                    gzu=gzu.name if gzu else None,
                    lift_type=item.lift,
                    qn=round(item.oil, 3),
                    water_cut=_round(item.water_cut, 1),
                    submergence_m=_round(result.submergence, 1),
                    speed=round(tel.speed, 1) if tel and tel.speed is not None else 0.0,
                    step_percent=result.step_percent,
                    gain=round(result.gain, 3),
                    # Предел скорости телеметрия не даёт (методика, п. 11.1).
                    speed_margin_checked=False,
                    risk=risk_for_step(result.step_percent),
                    pool_date=now.date(),
                    source_row=source_row,
                    in_pool=result.in_pool,
                    has_telemetry=result.has_telemetry,
                    gzu_abai_id=gzu.abai_id if gzu else None,
                    tap=gzu.tap if gzu else None,
                    calculated_at=now,
                ),
            )
        # Доноры прошлых расчётов, которые сейчас не работают, — тоже вне пула.
        scope = {well.id for well in data.wells}
        for well_id in {w for w, (in_pool, _at) in existing.items() if in_pool} - scope:
            out_of_pool[well_id] = {
                "method": METHOD,
                "calculated_at": now.isoformat(timespec="minutes"),
                "fails": ["не в работе"],
            }

        by_ngdu = defaultdict(dict)
        for well in data.wells:
            by_ngdu[well.abai_ngdu_id][well.id] = results[well.id]
        report = {
            "now": now.isoformat(timespec="minutes"),
            "pool": sum(row.in_pool for row in rows),
            "no_telemetry": sum(not row.has_telemetry for row in rows),
            "out_of_pool": len(out_of_pool),
            "blocked": drop_guard(
                {w for w, (in_pool, at) in existing.items() if in_pool and at},
                {row.well_id for row in rows if row.in_pool},
            ),
            "funnel": {
                NGDU_TITLES.get(ngdu, ngdu): funnel(res)
                for ngdu, res in by_ngdu.items()
            },
        }
        if dry_run:
            await app.rollback()
            return report
        if report["blocked"] and not force:
            await app.rollback()
            logger.error(
                "Donor pool %s not saved: %s",
                report["now"],
                report["blocked"],
            )
            return report
        repository = CompensationDonorRepository(app)
        for row in rows:
            await repository.upsert(row)
        await repository.mark_out_of_pool(out_of_pool, calculated_at=now)
        await app.commit()
    logger.info(
        "Donor pool %s: in_pool=%s, no_telemetry=%s, out_of_pool=%s",
        report["now"],
        report["pool"],
        report["no_telemetry"],
        report["out_of_pool"],
    )
    return report


def _print(report: dict) -> None:
    print(f"Пул доноров на {report['now']} (сухой прогон — ничего не записано)")  # noqa: T201
    for ngdu, steps in report["funnel"].items():
        print(f"\n{ngdu}:")  # noqa: T201
        for title, count in steps:
            print(f"  {title:<32} {count}")  # noqa: T201
    print(  # noqa: T201
        f"\nв пуле {report['pool']}, без телеметрии {report['no_telemetry']}, "
        f"выпадут из пула {report['out_of_pool']}; критериев: {len(CRITERIA)}",
    )
    if report["blocked"]:
        print(f"ЗАЩИТА: {report['blocked']} — без --force не запишется")  # noqa: T201


async def main(*, dry_run: bool = False, force: bool = False) -> None:
    report = await build_pool(dry_run=dry_run, force=force)
    if dry_run:
        _print(report)


@celery_app.task(name="compensation.build_pool")
def build_compensation_pool() -> None:
    run_async(main())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="записать, даже если из пула выпадает больше половины доноров",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, force=args.force))
