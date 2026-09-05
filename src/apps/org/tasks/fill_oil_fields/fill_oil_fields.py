"""Заполнение ``oil_fields`` по текущим привязкам скважин к НГДУ.

Месторождение здесь — буквенный префикс имени скважины (``BLG`` в
``BLG_0177``). Для каждой неудалённой скважины берётся текущая привязка из
``wells_well_org``, организация поднимается по ``parent_id`` до НГДУ, и пара
``(prefix, ngdu_id)`` попадает в желаемый набор. Один префикс может числиться
за несколькими НГДУ — уникальность в таблице именно по паре.

Только добавляет недостающие пары с ``name = prefix``. Существующие строки не
трогает (``name`` могут поправить руками), устаревшие пары логирует, но не
удаляет. Скважины без привязки, без буквенного префикса или с организацией,
не приводящей к НГДУ, считаются и попадают в итоговый лог.

Предварительно должны быть залиты ``org`` (org.sync) и ``wells_well_org``
(wells.well_org.incremental_load).

ТОЛЬКО РУЧНОЙ ЗАПУСК. Скрипт намеренно не зарегистрирован как Celery-таска и
не стоит в beat-расписании: справочник месторождений заполняется и правится
руками, автоматическое пополнение не нужно. Запуск:

    cd src && python -m apps.org.tasks.fill_oil_fields.fill_oil_fields
"""

import asyncio
import re
from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from apps.models_registry import *  # noqa: F403
from apps.org.dto.internal.repositories.oil_field import CreateOilFieldDTO
from apps.org.models.org import Org
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.wells.repositories.well_org import WellOrgRepository
from core import get_logger
from shared.constants.ngdu import NGDU_ORG_TYPE
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

# Буквенный префикс до подчёркивания: BLG_0177 -> BLG. Имена вида 00001-CT
# префикса не имеют и в месторождения не попадают.
PREFIX_RE = re.compile(r"^([A-Za-z]+)_")


class FillOilFields:
    MAX_PARENT_HOPS = 20

    def __init__(self, app_session: AsyncSession) -> None:
        self.oil_field_repo = OilFieldRepository(session=app_session)
        self.well_org_repo = WellOrgRepository(session=app_session)
        self.org_repo = OrgRepository(session=app_session)
        self.app_session = app_session

    async def run(self) -> None:
        org_by_abai_id = {org.abai_id: org for org in await self.org_repo.list_all()}
        org_by_well_name = await self.well_org_repo.list_current_org_by_well_name()

        wells_by_pair: Counter[tuple[str, int]] = Counter()
        without_prefix = 0
        without_ngdu = 0
        for well_name, abai_org_id in org_by_well_name.items():
            prefix = self._prefix_of(well_name)
            if prefix is None:
                without_prefix += 1
                continue
            ngdu = self._resolve_ngdu(abai_org_id, org_by_abai_id)
            if ngdu is None:
                without_ngdu += 1
                continue
            wells_by_pair[(prefix, ngdu.id)] += 1

        existing = await self.oil_field_repo.list_oil_fields()
        existing_pairs = {(row.prefix, row.ngdu_id) for row in existing}
        desired_pairs = set(wells_by_pair)
        to_create = sorted(desired_pairs - existing_pairs)
        stale = sorted(existing_pairs - desired_pairs)

        if stale:
            logger.warning("Oil fields without wells (kept): %s", stale)

        try:
            if to_create:
                await self.oil_field_repo.batch_create(
                    [
                        CreateOilFieldDTO(prefix=prefix, name=prefix, ngdu_id=ngdu_id)
                        for prefix, ngdu_id in to_create
                    ],
                )
                await self.app_session.commit()
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while filling oil fields")
            raise

        for prefix, ngdu_id in to_create:
            logger.info(
                "Oil field added: %s -> ngdu_id=%s (%s wells)",
                prefix,
                ngdu_id,
                wells_by_pair[(prefix, ngdu_id)],
            )
        logger.info(
            "Oil fields sync finished: created=%s, existing=%s, stale=%s, "
            "wells_without_prefix=%s, wells_without_ngdu=%s, wells_bound=%s",
            len(to_create),
            len(existing_pairs),
            len(stale),
            without_prefix,
            without_ngdu,
            len(org_by_well_name),
        )

    @staticmethod
    def _prefix_of(well_name: str) -> str | None:
        match = PREFIX_RE.match(well_name)
        return match.group(1) if match else None

    def _resolve_ngdu(
        self,
        abai_org_id: int,
        org_by_abai_id: dict[int, Org],
    ) -> Org | None:
        """Подняться по parent_id (хранит ABAI id) до организации типа НГДУ."""
        org = org_by_abai_id.get(abai_org_id)
        hops = 0
        while org is not None and org.org_type_id != NGDU_ORG_TYPE:
            hops += 1
            if hops > self.MAX_PARENT_HOPS or org.parent_id is None:
                return None
            org = org_by_abai_id.get(org.parent_id)
        return org


async def main() -> None:
    async with session_makers["app"]() as app_session:
        await FillOilFields(app_session=app_session).run()


# Ручной запуск: python -m apps.org.tasks.fill_oil_fields.fill_oil_fields
if __name__ == "__main__":
    asyncio.run(main())
