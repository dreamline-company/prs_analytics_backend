"""Техрежимы из ABAI (``emg.tech_mode_prod_oil``).

1. Новые: keyset по ``id > max(abai_id)`` батчами.
2. Перечитка действующих: режимы ABAI, действующие в последние
   ``REREAD_DAYS`` (``dend`` не раньше), сверяются с копией. Технологи правят
   план и закрывают режим правкой существующей строки — новый id при этом не
   появляется, и шаг 1 правку не увидит; строки, закоммиченные позже с меньшим
   id, шаг 1 тоже пропускает (на 04.11.2026 расходились 27 режимов и 162 не
   было вовсе). Изменённые обновляются, недостающие создаются, исчезнувшие в
   ABAI удаляются — если их не больше ``MAX_GONE`` / ``MAX_GONE_SHARE``
   (иначе это сбой чтения, ничего не удаляется).

Режимы по скважине, которой нет в ``wells_well`` (``load_wells`` ручной), и без
даты окончания пропускаются: FK и NOT NULL иначе роняли бы весь прогон.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import or_

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.telemetry.dto.internal.repositories import CreateTechRegimeDTO
from apps.telemetry.dto.internal.repositories.tech_regime import UpdateTechRegimeDTO
from apps.telemetry.models.tech_regime import TechRegime
from apps.telemetry.repositories import TechRegimeRepository
from apps.wells.repositories import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import TechModeProdOil
from shared.integrations.abai.repositories import ABAITechModeProdOilRepository
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()


def regime_dto(row: TechModeProdOil) -> CreateTechRegimeDTO:
    return CreateTechRegimeDTO(
        abai_id=row.id,
        abai_well_id=row.well,
        start_date=row.dbeg,
        end_date=row.dend,
        oil=row.oil,
        liquid=row.liquid,
    )


def regime_changed(local: TechRegime, row: TechModeProdOil) -> bool:
    return (
        local.abai_well_id != row.well
        or local.start_date != row.dbeg
        or local.end_date != row.dend
        or local.oil != row.oil
        or local.liquid != row.liquid
    )


class ABAILoadTechRegime:
    ITER_BATCH_SIZE = 5000
    REREAD_DAYS = 60
    MAX_GONE = 50
    MAX_GONE_SHARE = 0.05

    async def run(self, *, today: date | None = None) -> None:
        today = today or datetime.now(tz=settings.ZONE_INFO).date()
        async with (
            session_makers["app"]() as app_session,
            session_makers["abai"]() as abai_session,
        ):
            app_tech_repo = TechRegimeRepository(app_session)
            abai_tech_repo = ABAITechModeProdOilRepository(abai_session)
            self.known_wells = await WellRepository(app_session).list_abai_ids()
            try:
                last_tech_regime = await app_tech_repo.get_latest_by_abai_id()
                last_tech_abai_id = last_tech_regime.abai_id if last_tech_regime else 0
                count = skipped = 0
                async for regimes in self._iter_abai_tech_regimes(
                    abai_tech_repo,
                    last_tech_abai_id,
                ):
                    loadable = [r for r in regimes if self._is_loadable(r)]
                    skipped += len(regimes) - len(loadable)
                    await app_tech_repo.bulk_create([regime_dto(r) for r in loadable])
                    count += len(loadable)
                created, updated, deleted = await self._reread(
                    abai_tech_repo,
                    app_tech_repo,
                    since=today - timedelta(days=self.REREAD_DAYS),
                )
                await app_session.commit()
            except Exception:
                logger.exception("Error while loading tech regimes")
                await app_session.rollback()
                raise
        logger.info(
            "Tech regimes: loaded=%s, skipped=%s; reread: created=%s, updated=%s, "
            "deleted=%s",
            count,
            skipped,
            created,
            updated,
            deleted,
        )

    def _is_loadable(self, row: TechModeProdOil) -> bool:
        return row.well in self.known_wells and row.dend is not None

    async def _reread(
        self,
        abai_tech_repo: ABAITechModeProdOilRepository,
        app_tech_repo: TechRegimeRepository,
        *,
        since: date,
    ) -> tuple[int, int, int]:
        model = abai_tech_repo.model
        source = {
            row.id: row
            for row in await abai_tech_repo.get_list(
                spec=QuerySpec(
                    filters=(or_(model.dend >= since, model.dend.is_(None)),),
                    order_by=(model.id.asc(),),
                ),
            )
        }
        local_recent = await app_tech_repo.get_list(
            QuerySpec(filters=(TechRegime.end_date >= since,)),
        )
        # Действующий у нас, но не в выборке: в ABAI закрыт раньше ``since``
        # (правка) или удалён — дочитываем по id.
        absent = [row.abai_id for row in local_recent if row.abai_id not in source]
        if absent:
            for row in await abai_tech_repo.get_list(
                spec=QuerySpec(filters=(model.id.in_(absent),)),
            ):
                source[row.id] = row
        local = {
            row.abai_id: row
            for row in await app_tech_repo.list_by_abai_ids(list(source))
        }
        local.update({row.abai_id: row for row in local_recent})

        create = [
            regime_dto(row)
            for abai_id, row in source.items()
            if abai_id not in local and self._is_loadable(row)
        ]
        update = [
            (local[abai_id], row)
            for abai_id, row in source.items()
            if abai_id in local
            and self._is_loadable(row)
            and regime_changed(local[abai_id], row)
        ]
        gone = [row for abai_id, row in local.items() if abai_id not in source]

        await app_tech_repo.bulk_create(create)
        for local_row, row in update:
            await app_tech_repo.update_by_id(
                local_row.id,
                UpdateTechRegimeDTO(
                    abai_well_id=row.well,
                    start_date=row.dbeg,
                    end_date=row.dend,
                    oil=row.oil,
                    liquid=row.liquid,
                ),
            )
        if len(gone) > max(self.MAX_GONE, len(local_recent) * self.MAX_GONE_SHARE):
            logger.error(
                "Tech regimes: %s of %s current regimes not found in ABAI — "
                "source read failure suspected, nothing deleted",
                len(gone),
                len(local_recent),
            )
            gone = []
        for row in gone:
            await app_tech_repo.delete_by_id(row.id)
        return len(create), len(update), len(gone)

    async def _iter_abai_tech_regimes(
        self,
        abai_tech_repo: ABAITechModeProdOilRepository,
        last_regime_id: int,
    ) -> AsyncGenerator[Sequence[TechModeProdOil]]:
        last_id = last_regime_id
        while True:
            regimes = await abai_tech_repo.get_list(
                spec=QuerySpec(
                    filters=(abai_tech_repo.model.id > last_id,),
                    limit=self.ITER_BATCH_SIZE,
                    order_by=(abai_tech_repo.model.id.asc(),),
                ),
            )
            if not regimes:
                break
            yield regimes
            last_id = regimes[-1].id
            if len(regimes) < self.ITER_BATCH_SIZE:
                break


async def main() -> None:
    await ABAILoadTechRegime().run()


@celery_app.task(name="telemetry.tech_regime.incremental_load")
def load_tech_regime_incremental() -> None:
    """Инкремент техрежимов из ABAI и перечитка действующих за 60 дней."""
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
