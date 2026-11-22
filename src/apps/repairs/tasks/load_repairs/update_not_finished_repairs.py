"""Актуализация незавершённых ремонтов по ABAI.

Проверяются все идущие ремонты, без ограничения по давности: их десятки, а
ремонт, закрытый в ABAI спустя полгода, иначе висел бы у нас открытым вечно.
Ремонт, которого в ABAI больше нет, помечается ``abai_deleted_at`` и перестаёт
считаться идущим; запись остаётся ради привязанной аналитики, СПО и сводок.
Пропало больше ``MAX_GONE`` / ``MAX_GONE_SHARE`` — это сбой ответа ABAI, а не
удаления: ничего не помечается. Помеченный ремонт, снова найденный в ABAI,
пометку теряет (ложная пометка иначе была бы вечной).
Ремонты, закрытые нами по телеметрии (``closed_by_telemetry_at``), тоже
сверяются: дата окончания из ABAI заменяет нашу, пометка снимается.

Сработавшая защита (``too_many_gone``) шлёт ``feed.abai_guard`` (``notify=True``
— плановый ``repairs.sync``), не чаще раза в час. В число пропавших для
уведомления входят и батчи, на которые ABAI ответил пусто: их ремонты не
помечаются и сейчас, но молча.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.models_registry import *  # noqa: F403
from apps.notifications.constants import local_now
from apps.notifications.producers.common import emit_all
from apps.notifications.producers.feeds import (
    ABAI_GUARD_REPAIRS,
    abai_guard_notification,
)
from apps.notifications.services.publisher import publish_notifications
from apps.repairs.dto.internal.repositories.repair import UpdateRepairDTO
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.repair import RepairRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories import ABAIWellWorkoverRepository
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()


def too_many_gone(gone: int, checked: int) -> bool:
    """Столько пропавших за прогон — сбой источника, а не удаления."""
    return gone > max(
        UpdateNotFinishedRepairs.MAX_GONE,
        checked * UpdateNotFinishedRepairs.MAX_GONE_SHARE,
    )


class UpdateNotFinishedRepairs:
    ITER_SIZE = 1000
    # Настоящие удаления — единицы в месяц (на 04.11.2026 всего 10).
    MAX_GONE = 3
    MAX_GONE_SHARE = 0.1

    def __init__(self, *, notify: bool = False) -> None:
        self.notify = notify

    async def run(self) -> None:
        logger.info("UpdateNotFinishedRepairs task started.")
        now = datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        async with session_makers["abai"]() as abai_session:
            abai_repairs_repo = ABAIWellWorkoverRepository(abai_session)
            updated = deleted = restored = checked = unanswered = 0
            gone: list[int] = []
            async with session_makers["app"]() as app_session:
                app_repairs_repo = RepairRepository(app_session)
                try:
                    async for nf_repairs in self._iter_nf_repairs(app_repairs_repo):
                        nf_repairs_abai_ids = [r.abai_id for r in nf_repairs]
                        checked += len(nf_repairs_abai_ids)
                        logger.debug(
                            "Batch: %s non-finished repairs fetched from app DB.",
                            len(nf_repairs),
                        )
                        abai_repairs = await abai_repairs_repo.list_by_ids(
                            nf_repairs_abai_ids,
                        )
                        logger.debug(
                            "Matched %s repairs in ABAI for this batch.",
                            len(abai_repairs),
                        )
                        # Пустой ответ на весь батч — скорее сбой ABAI, чем
                        # удаление всех ремонтов разом: ничего не помечаем.
                        if not abai_repairs:
                            unanswered += len(nf_repairs_abai_ids)
                            continue
                        found_ids = {ar.id for ar in abai_repairs}
                        gone.extend(
                            abai_id
                            for abai_id in nf_repairs_abai_ids
                            if abai_id not in found_ids
                        )
                        for ar in abai_repairs:
                            if ar.dend is not None and isinstance(ar.dend, datetime):
                                logger.debug(
                                    "Updating repair abai_id=%s → end_time=%s.",
                                    ar.id,
                                    ar.dend,
                                )
                                await app_repairs_repo.update_by_abai_id(
                                    abai_id=ar.id,
                                    data=UpdateRepairDTO(
                                        closed_by_telemetry_at=None,
                                        end_time=ar.dend,
                                        work_plan=ar.work_plan,
                                        work_list=ar.work_list,
                                        abai_repair_type_id=ar.repair_type,
                                    ),
                                )
                                updated += 1
                    deleted = await self._mark_gone(
                        app_repairs_repo,
                        gone,
                        checked=checked,
                        now=now,
                    )
                    restored = await self._unmark_found(
                        app_repairs_repo,
                        abai_repairs_repo,
                    )
                    ids = await self._emit_guard(
                        app_session,
                        gone=len(gone) + unanswered,
                        checked=checked,
                        marked=deleted,
                    )
                    if updated or deleted or restored:
                        await app_session.commit()

                        logger.info(
                            "Updated %s repairs and set as finished; "
                            "%s marked as deleted in ABAI, %s unmarked.",
                            updated,
                            deleted,
                            restored,
                        )
                    elif ids:
                        await app_session.commit()
                    logger.info(
                        "UPdating of non finished repairs is done successfully.",
                    )
                except Exception:
                    logger.exception("Update of Not finished repairs is failed.")
                    await app_session.rollback()
                else:
                    await self._publish(ids)

    async def _emit_guard(
        self,
        session: AsyncSession,
        *,
        gone: int,
        checked: int,
        marked: int,
    ) -> list[int]:
        """Защита сработала (пропало слишком много, ничего не помечено) —
        ``feed.abai_guard`` прямо перед commit."""
        if not self.notify or marked or not too_many_gone(gone, checked):
            return []
        try:
            return await emit_all(
                session,
                [
                    abai_guard_notification(
                        guard=ABAI_GUARD_REPAIRS,
                        gone=gone,
                        checked=checked,
                        now=local_now(),
                    ),
                ],
            )
        except Exception:
            logger.exception("ABAI guard notification not written")
            return []

    @staticmethod
    async def _publish(ids: list[int]) -> None:
        if not ids:
            return
        try:
            await publish_notifications(ids)
        except Exception:
            logger.exception("ABAI guard notification signal failed")

    async def _mark_gone(
        self,
        repairs_repo: RepairRepository,
        gone: list[int],
        *,
        checked: int,
        now: datetime,
    ) -> int:
        if too_many_gone(len(gone), checked):
            logger.error(
                "%s of %s open repairs not found in ABAI — source read failure "
                "suspected, nothing marked as deleted",
                len(gone),
                checked,
            )
            return 0
        for abai_id in gone:
            logger.info("Repair abai_id=%s is gone from ABAI; marked.", abai_id)
            await repairs_repo.update_by_abai_id(
                abai_id=abai_id,
                data=UpdateRepairDTO(abai_deleted_at=now),
            )
        return len(gone)

    async def _unmark_found(
        self,
        repairs_repo: RepairRepository,
        abai_repairs_repo: ABAIWellWorkoverRepository,
    ) -> int:
        marked = await repairs_repo.get_list(
            spec=QuerySpec(filters=(Repair.abai_deleted_at.is_not(None),)),
        )
        if not marked:
            return 0
        found = {
            row.id
            for row in await abai_repairs_repo.list_by_ids(
                [repair.abai_id for repair in marked],
            )
        }
        for abai_id in sorted(found):
            logger.warning("Repair abai_id=%s is back in ABAI; unmarked.", abai_id)
            await repairs_repo.update_by_abai_id(
                abai_id=abai_id,
                data=UpdateRepairDTO(abai_deleted_at=None),
            )
        return len(found)

    async def _iter_nf_repairs(
        self,
        repairs_repo: RepairRepository,
    ) -> AsyncIterator[Sequence[Repair]]:
        last_r_abai_id = None
        while True:
            filters = []
            if last_r_abai_id is not None:
                filters.append(repairs_repo.model.abai_id > last_r_abai_id)
            nf_repairs = await repairs_repo.get_list(
                spec=QuerySpec(
                    filters=(
                        *filters,
                        or_(
                            repairs_repo.model.is_open,
                            repairs_repo.model.closed_by_telemetry_at.is_not(None),
                        ),
                    ),
                    order_by=(repairs_repo.model.abai_id.asc(),),
                    limit=self.ITER_SIZE,
                ),
            )
            if not nf_repairs:
                break
            yield nf_repairs
            last_r_abai_id = nf_repairs[-1].abai_id
            if len(nf_repairs) < self.ITER_SIZE:
                break


async def main(*, notify: bool = False) -> None:
    await UpdateNotFinishedRepairs(notify=notify).run()


if __name__ == "__main__":
    asyncio.run(main())
