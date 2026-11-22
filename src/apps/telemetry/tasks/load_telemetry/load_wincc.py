"""Инкремент замеров дебитов из WinCC/ЦИТС четырёх НГДУ.

Каждый прогон перечитывает последние ``OVERLAP`` суток источника, а не только
замеры позже последнего загруженного: ЦИТС дописывает замеры задним числом
(ГЗУ выгружаются с опозданием), и строгий курсор их терял. Уже загруженные
строки узнаются по паре (скважина, время замера) и пропускаются.

    python -m apps.telemetry.tasks.load_telemetry.load_wincc
    python -m apps.telemetry.tasks.load_telemetry.load_wincc \\
        --ngdu ZHMG --since 2023-11-01

Второй вариант — разовая догрузка истории (например, после правки
сопоставления месторождений): источник перечитывается с даты, недостающее
дописывается, дублей не появляется.

Замеры с датой позже ``future_limit()`` (опечатка в ЦИТС) не грузятся и не
сдвигают курсор: иначе загрузка НГДУ встала бы до этой даты.

Плановый прогон Celery (``notify=True``) после каждого НГДУ — и после
упавшей загрузки тоже — сверяет последний замер НГДУ у нас: нет новых
замеров ``CITS_STALE_AFTER`` — ``feed.cits_stale``, снова пошли —
``feed.cits_restored`` (``producers.feeds``). Ручной запуск и догрузка
истории (``--since``) не шлют.
"""

import argparse
import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime, timedelta

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.notifications.constants import local_now
from apps.notifications.producers.common import SOURCE_FEED_CITS, dedup_key, emit_all
from apps.notifications.producers.feeds import (
    CITS_CLOCK_TOLERANCE,
    EVENT_CITS_RESTORED,
    EVENT_CITS_STALE,
    CitsLoadResult,
    cits_feed_notification,
    cits_stale_ref,
)
from apps.notifications.repositories.notification import NotificationRepository
from apps.notifications.services.publisher import publish_notifications
from apps.telemetry.dto.internal.repositories import CreateTelemetryDTO
from apps.telemetry.repositories import TelemetryRepository
from apps.telemetry.services.clock import future_limit
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.sql.setup import session_makers
from shared.integrations.wincc.models import NGDUWinccTelemetryModel
from shared.integrations.wincc.repositories import (
    DMGWinccTelemetryRepository,
    KainarWinccTelemetryRepository,
    NGDUWinccTelemetryRepository,
)
from shared.integrations.wincc.repositories.zhmg_telemetry import (
    ZHMGWinccTelemetryRepository,
)
from shared.integrations.wincc.repositories.zhylmg_telemetry import (
    ZHYLMGWinccTelemetryRepository,
)
from shared.repository.sqlalchemy import QuerySpec
from utils.wells import make_code

logger = get_logger(__name__)

# Месторождения, которые ЦИТС называет не так, как ABAI: код в поле Oil_field
# источника -> префикс имени скважины у нас. ЖМГ пишет месторождение ZHT как
# ZNT; без замены его замеры не находили скважину и отбрасывались целиком.
WINCC_FIELD_ALIASES = {"ZNT": "ZHT"}

NGDU_NAMES = ("KMG", "DMG", "ZHMG", "ZHLMG")
NGDU_IDS = {
    "KMG": AbaiNGDUIDsEnum.KMG,
    "DMG": AbaiNGDUIDsEnum.DMG,
    "ZHMG": AbaiNGDUIDsEnum.ZHMG,
    "ZHLMG": AbaiNGDUIDsEnum.ZHlMG,
}

type TelemetryKey = tuple[int, datetime]


def wincc_well_name(oil_field: str, well: str) -> str:
    """Имя скважины (``UZK_0377``) по месторождению и номеру из WinCC.

    Raises:
        ValueError: Номер цифровой, но код месторождения не собрать в имя.
    """
    field = oil_field.strip().upper()
    field = WINCC_FIELD_ALIASES.get(field, field)
    if well.strip().isdigit():
        return make_code(field, well)
    return field + "_" + well.strip()


class WinccLoadTelemetry:
    ITER_BATCH_SIZE = 50_000
    # Сколько последних суток источника перечитывать на каждом прогоне.
    OVERLAP = timedelta(days=7)

    def __init__(
        self,
        *,
        ngdus: Sequence[str] = NGDU_NAMES,
        since: datetime | None = None,
        notify: bool = False,
    ) -> None:
        self.ngdus = tuple(ngdus)
        # Явная дата — разовая догрузка истории вместо перечитки окна.
        self.since = since
        # Уведомления о сбое ЦИТС — только плановый прогон, не догрузка.
        self.notify = notify and since is None
        self.load_results: dict[int, CitsLoadResult] = {}

    async def run(self) -> None:
        async with session_makers["app"]() as app_session:
            app_wells_repo = WellRepository(app_session)
            app_wells = await app_wells_repo.get_list()
            app_wells_ids = {well.name: well.id for well in app_wells}
            del app_wells

        loaders = {
            "KMG": self._load_kainar,
            "DMG": self._load_dmg,
            "ZHMG": self._load_zhmg,
            "ZHLMG": self._load_zhylmg,
        }
        # Источники независимы: недоступный WinCC одного НГДУ не должен
        # оставлять остальные без свежих замеров до следующего запуска.
        for name in self.ngdus:
            try:
                await loaders[name](app_wells_ids)
            except Exception as error:
                logger.exception("WinCC telemetry load [%s] failed; continuing", name)
                self.load_results.setdefault(
                    NGDU_IDS[name],
                    CitsLoadResult(error=repr(error)),
                )
            if self.notify:
                await self._check_cits_feed(NGDU_IDS[name])

    async def _check_cits_feed(self, ngdu_id: int) -> None:
        """Свежесть замеров НГДУ у нас → ``feed.cits_stale`` / ``feed.cits_restored``.

        Своя короткая сессия только для уведомления; сбой пишется в лог и
        загрузку не роняет. Последний замер — по индексу
        ``(abai_ngdu_id, date_time)``, не позже «сейчас» + час (а не
        ``future_limit()`` загрузки), открытый сбой — по журналу.
        """
        try:
            ids: list[int] = []
            now = local_now()
            async with session_makers["app"]() as session:
                last_tm = await TelemetryRepository(session).get_last_by_ngdu_id(
                    abai_ngdu_id=ngdu_id,
                    until=now + CITS_CLOCK_TOLERANCE,
                )
                if last_tm is None:
                    return
                journal = NotificationRepository(session)
                row = await journal.get_last_feed_row(
                    EVENT_CITS_STALE,
                    source_type=SOURCE_FEED_CITS,
                    abai_ngdu_id=ngdu_id,
                )
                previous = None
                if row is not None and row.source_id is not None:
                    previous = cits_stale_ref(
                        source_id=row.source_id,
                        payload=row.payload,
                        restored=await journal.has_dedup_key(
                            dedup_key(
                                EVENT_CITS_RESTORED,
                                SOURCE_FEED_CITS,
                                row.source_id,
                            ),
                        ),
                    )
                item = cits_feed_notification(
                    abai_ngdu_id=ngdu_id,
                    last_measure_at=last_tm.date_time,
                    now=now,
                    previous=previous,
                    load=self.load_results.get(ngdu_id),
                )
                if item is None:
                    return
                ids = await emit_all(session, [item])
                await session.commit()
            await publish_notifications(ids)
        except Exception:
            logger.exception("CITS feed check for NGDU #%s failed", ngdu_id)

    async def _load_dmg(
        self,
        app_wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading DMG...")
        async with session_makers["dmg_telemetry"]() as dmg_session:
            dmg_tm_repo = DMGWinccTelemetryRepository(dmg_session)
            await self._load_ngdu(
                ngdu_id=AbaiNGDUIDsEnum.DMG,
                app_wells_ids=app_wells_ids,
                ngdu_tm_repo=dmg_tm_repo,
            )

    async def _load_kainar(
        self,
        app_wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading Kainar...")
        async with session_makers["kainar_telemetry"]() as kainar_session:
            kainar_tm_repo = KainarWinccTelemetryRepository(kainar_session)
            await self._load_ngdu(
                ngdu_id=AbaiNGDUIDsEnum.KMG,
                app_wells_ids=app_wells_ids,
                ngdu_tm_repo=kainar_tm_repo,
            )

    async def _load_zhmg(
        self,
        app_wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading ZHMG...")
        async with session_makers["zhmg_telemetry"]() as dmg_session:
            zhmg_tm_repo = ZHMGWinccTelemetryRepository(dmg_session)
            await self._load_ngdu(
                ngdu_id=AbaiNGDUIDsEnum.ZHMG,
                app_wells_ids=app_wells_ids,
                ngdu_tm_repo=zhmg_tm_repo,
            )

    async def _load_zhylmg(
        self,
        app_wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading ZHYLMG...")
        async with session_makers["zhylmg_telemetry"]() as dmg_session:
            zhylmg_tm_repo = ZHYLMGWinccTelemetryRepository(dmg_session)
            await self._load_ngdu(
                ngdu_id=AbaiNGDUIDsEnum.ZHlMG,
                app_wells_ids=app_wells_ids,
                ngdu_tm_repo=zhylmg_tm_repo,
            )

    async def _load_ngdu(
        self,
        ngdu_id: int,
        ngdu_tm_repo: NGDUWinccTelemetryRepository,
        app_wells_ids: dict[str, int],
    ) -> None:

        async with session_makers["app"]() as app_session:
            telemetry_repo = TelemetryRepository(app_session)
            since = self.since
            if since is None:
                last_tm = await telemetry_repo.get_last_by_ngdu_id(
                    abai_ngdu_id=ngdu_id,
                    until=future_limit(),
                )
                since = last_tm.date_time - self.OVERLAP if last_tm else None
            existing = (
                await telemetry_repo.list_keys_since(ngdu_id, since)
                if since is not None
                else set()
            )
            try:
                c = 0
                n = 0
                added = 0
                async for tms in self._iter_tm(ngdu_tm_repo, since=since):
                    c += 1
                    n += len(tms)
                    saved = await self._save_tm(
                        tms,
                        app_wells_ids,
                        ngdu_id,
                        telemetry_repo,
                        existing,
                    )
                    if saved:
                        added += saved
                        await app_session.commit()
                    logger.debug(
                        "NGDU: %s Number: %s Iterations: %s Saved: %s",
                        ngdu_id,
                        n,
                        c,
                        saved,
                    )
            except Exception as error:
                logger.exception("Error while loading NGDU #%s telemetry.", ngdu_id)
                await app_session.rollback()
                self.load_results[ngdu_id] = CitsLoadResult(
                    read=n,
                    added=added,
                    error=repr(error),
                )
            else:
                self.load_results[ngdu_id] = CitsLoadResult(read=n, added=added)
                logger.info(
                    "NGDU #%s telemetry loaded: read=%s, added=%s, since=%s",
                    ngdu_id,
                    n,
                    added,
                    since,
                )

    @classmethod
    async def _save_tm(
        cls,
        tms: Sequence[NGDUWinccTelemetryModel],
        app_wells_ids: dict[str, int],
        dmg_ngdu_id: int,
        app_telemetry_repo: TelemetryRepository,
        existing: set[TelemetryKey],
    ) -> int:
        """Записать новые замеры батча; вернуть, сколько добавлено.

        ``existing`` — уже загруженные пары (скважина, время замера); сюда же
        дописываются добавленные, чтобы перекрытие батчей не дало дублей.
        """
        bulk_data = []
        not_found_wells = []
        limit = future_limit()
        future = 0
        for tm in tms:
            if (
                not tm.Meas_date
                or not tm.Well
                or not tm.Oil_field
                or tm.Well == ""
                or tm.Oil_field == ""
            ):
                logger.warning(
                    "TM skipped. Oilfield: %s, well: %s",
                    tm.Oil_field,
                    tm.Well,
                )
                continue
            if tm.Meas_date > limit:
                future += 1
                continue

            try:
                well_name = wincc_well_name(tm.Oil_field, tm.Well)
            except ValueError:
                logger.exception(
                    "TM skipped. Could form well name %s %s",
                    tm.Oil_field,
                    tm.Well,
                )
                continue
            well_id = app_wells_ids.get(well_name)
            if not well_id:
                not_found_wells.append(well_name)
                continue
            key = (well_id, tm.Meas_date)
            if key in existing:
                continue
            existing.add(key)
            bulk_data.append(
                CreateTelemetryDTO(
                    well_id=well_id,
                    date_time=tm.Meas_date,
                    qv_liquid=tm.Qv_liq,
                    qm_oil=tm.Qm_oil,
                    qv_water=tm.Qv_water,
                    qm_water=tm.Qm_water,
                    abai_ngdu_id=dmg_ngdu_id,
                    oil_field=tm.Oil_field,
                ),
            )
        if future:
            logger.warning("TM skipped: %s measurements dated in the future", future)
        if not_found_wells:
            logger.warning("Not found %s wells in database", len(not_found_wells))
        if bulk_data:
            logger.debug("Bulking: %s", len(bulk_data))
            await app_telemetry_repo.bulk_create(data=bulk_data)
        return len(bulk_data)

    async def _iter_tm(
        self,
        wincc_tm_repo: NGDUWinccTelemetryRepository,
        since: datetime | None,
    ) -> AsyncGenerator[Sequence[NGDUWinccTelemetryModel]]:
        """Строки источника с ``Meas_date >= since`` батчами по времени.

        Граница батча включается повторно (``>=``): строки с тем же временем,
        не влезшие в предыдущий батч, иначе терялись бы. Повторы отсекает
        проверка по уже загруженным ключам.
        """
        last_time = since
        strict = False
        while True:
            filters = []
            if last_time:
                meas_date = wincc_tm_repo.model.Meas_date
                filters = [meas_date > last_time if strict else meas_date >= last_time]
            tms = await wincc_tm_repo.get_list(
                spec=QuerySpec(
                    filters=(*filters, wincc_tm_repo.model.Well.isnot(None)),
                    limit=self.ITER_BATCH_SIZE,
                    order_by=(wincc_tm_repo.model.Meas_date.asc(),),
                ),
            )
            if not tms:
                break
            logger.debug("Selected tms: %s", len(tms))
            yield tms
            if len(tms) < self.ITER_BATCH_SIZE:
                break
            # Весь батч с одним временем — дальше строго, иначе цикл встанет.
            strict = tms[-1].Meas_date == last_time
            last_time = tms[-1].Meas_date


async def main(
    ngdus: Sequence[str] = NGDU_NAMES,
    since: datetime | None = None,
    *,
    notify: bool = False,
) -> None:
    await WinccLoadTelemetry(ngdus=ngdus, since=since, notify=notify).run()


@celery_app.task(name="telemetry.wincc.incremental_load")
def load_wincc_incremental() -> None:
    """Инкремент замеров дебитов из WinCC всех НГДУ (с перечиткой окна)."""
    run_async(main(notify=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ngdu",
        action="append",
        choices=NGDU_NAMES,
        help="НГДУ; можно повторять. По умолчанию — все четыре.",
    )
    parser.add_argument(
        "--since",
        type=datetime.fromisoformat,
        help="Перечитать источник с этой даты (разовая догрузка истории).",
    )
    args = parser.parse_args()
    asyncio.run(main(ngdus=args.ngdu or NGDU_NAMES, since=args.since))
