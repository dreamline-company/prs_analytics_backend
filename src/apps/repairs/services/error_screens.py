"""Экраны ошибок бригады за время ремонта — нарушения ТБ из CM.

Одна цепочка для KPI ремонта, общего ИИ-вердикта и страницы аналитики, чтобы
везде были одни и те же экраны: бригада ремонта (привязка из сводок) -> бригады
CM с её номером в её НГДУ -> их экраны за время ремонта (незакрытый ремонт — до
текущего момента). Нет данных на любом шаге — пусто.
"""

from collections.abc import Sequence
from datetime import datetime

from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.org.services.cm_brigades import match_cm_brigades
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from shared.integrations.cm.models import BrigadeErrorScreen
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository


async def list_repair_error_screens(
    repair: Repair,
    *,
    repair_brigade_repo: RepairBrigadeRepository,
    unique_brigade_repo: UniqueBrigadeRepository,
    cm_brigade_repo: CMBrigadeRepository,
    cm_brigade_error_screen_repo: CMBrigadeErrorScreenRepository,
) -> Sequence[BrigadeErrorScreen]:
    link = await repair_brigade_repo.get_by_repair_id(repair.id)
    if link is None:
        return ()
    brigade = await unique_brigade_repo.get_by_id(link.brigade_id)
    if brigade is None:
        return ()
    matched = await match_cm_brigades(
        [brigade],
        unique_brigade_repo=unique_brigade_repo,
        cm_brigade_repo=cm_brigade_repo,
    )
    cm_ids = matched.get(brigade.id)
    if not cm_ids:
        return ()
    end_time = repair.end_time or datetime.now()  # noqa: DTZ005
    return await cm_brigade_error_screen_repo.list_by_brigade_ids_in_range(
        cm_ids,
        start_time=repair.start_time,
        end_time=end_time,
    )
