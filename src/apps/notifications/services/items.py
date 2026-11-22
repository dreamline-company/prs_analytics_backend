"""Строки журнала → элементы выдачи с блоком ``well``.

Имя скважины, НГДУ (org.id + название) и способ эксплуатации подтягиваются
батчами на чтении: в журнале их нет. НГДУ — то, что записано в уведомлении
(НГДУ скважины на момент записи), способ эксплуатации — текущий, тем же
правилом, что в строке матрицы инцидентов.
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.constants import exploitation_code
from apps.notifications.dto.internal.notification import (
    NotificationFactDTO,
    NotificationRowDTO,
)
from apps.notifications.dto.responses.notification import (
    NotificationCardDTO,
    NotificationChainItemDTO,
    NotificationItemDTO,
    NotificationWellDTO,
    UnviewedCountsDTO,
)
from apps.org.repositories.org import OrgRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_expl import WellExplRepository
from core import get_logger

logger = get_logger(__name__)


def unviewed_counts(counts: dict[str, int]) -> UnviewedCountsDTO:
    return UnviewedCountsDTO(
        alarm=counts.get("alarm", 0),
        warning=counts.get("warning", 0),
        info=counts.get("info", 0),
    )


def chain_item(row: NotificationRowDTO) -> NotificationChainItemDTO:
    return NotificationChainItemDTO(
        id=row.id,
        event_code=row.event_code,
        severity=row.severity,
        title_ru=row.title_ru,
        event_at=row.event_at,
        created_at=row.created_at,
        viewed_at=row.viewed_at,
    )


def payload_facts(payload: dict) -> list[NotificationFactDTO]:
    """``payload.facts`` как список фактов; кривые записи пропускаются."""
    facts = []
    for raw in payload.get("facts") or []:
        try:
            facts.append(NotificationFactDTO.model_validate(raw))
        except ValueError:
            logger.warning("Skipping malformed notification fact: %r", raw)
    return facts


class NotificationItemBuilder:
    def __init__(
        self,
        *,
        well_repository: WellRepository,
        org_repository: OrgRepository,
        well_expl_repository: WellExplRepository,
    ) -> None:
        self.well_repository = well_repository
        self.org_repository = org_repository
        self.well_expl_repository = well_expl_repository

    @classmethod
    def for_session(cls, session: AsyncSession) -> "NotificationItemBuilder":
        return cls(
            well_repository=WellRepository(session),
            org_repository=OrgRepository(session),
            well_expl_repository=WellExplRepository(session),
        )

    async def build(
        self,
        rows: Sequence[NotificationRowDTO],
    ) -> list[NotificationItemDTO]:
        well_ids = sorted({row.well_id for row in rows if row.well_id is not None})
        wells = {w.id: w for w in await self.well_repository.list_by_ids(well_ids)}
        abai_well_ids = [w.abai_id for w in wells.values() if w.abai_id is not None]
        expl_names = (
            await self.well_expl_repository.get_latest_expl_name_by_abai_well_ids(
                abai_well_ids,
            )
        )
        abai_ngdu_ids = sorted(
            {row.abai_ngdu_id for row in rows if row.abai_ngdu_id is not None},
        )
        orgs = {
            org.abai_id: org
            for org in await self.org_repository.list_by_abai_ids(abai_ngdu_ids)
        }

        items = []
        for row in rows:
            well_dto = None
            well = wells.get(row.well_id) if row.well_id is not None else None
            if well is not None:
                org = orgs.get(row.abai_ngdu_id) if row.abai_ngdu_id else None
                well_dto = NotificationWellDTO(
                    id=well.id,
                    name=well.name,
                    ngdu_id=org.id if org is not None else None,
                    ngdu_name=org.name_ru if org is not None else None,
                    exploitation=exploitation_code(expl_names.get(well.abai_id)),
                )
            items.append(
                NotificationItemDTO(
                    id=row.id,
                    event_code=row.event_code,
                    severity=row.severity,
                    title_ru=row.title_ru,
                    body_ru=row.body_ru,
                    event_at=row.event_at,
                    created_at=row.created_at,
                    viewed_at=row.viewed_at,
                    source_type=row.source_type,
                    source_id=row.source_id,
                    well=well_dto,
                ),
            )
        return items

    async def card(
        self,
        row: NotificationRowDTO,
        chain: Sequence[NotificationRowDTO],
    ) -> NotificationCardDTO:
        (item,) = await self.build([row])
        return NotificationCardDTO(
            **item.model_dump(),
            payload=row.payload,
            facts=payload_facts(row.payload),
            chain=[chain_item(r) for r in chain],
        )
