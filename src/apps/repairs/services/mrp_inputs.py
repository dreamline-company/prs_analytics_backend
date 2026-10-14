"""Входные данные расчёта МРП из базы: ремонты, статусы «в работе», способ добычи.

Время: ремонты ABAI хранятся в местном наивном времени, статусы — в наивном
UTC (см. ``wells_well_status``), поэтому статусы приводятся к местному.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from apps.org.services import well_name_prefix
from apps.repairs.services.mrp import Interval, RepairSpan, WellFundInput
from apps.repairs.services.repair_classifier import is_gtm_repair
from core.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apps.repairs.repositories.repair import RepairRepository
    from apps.wells.models.well import Well
    from apps.wells.repositories.well_expl import WellExplRepository
    from apps.wells.repositories.well_status import WellStatusRepository

settings = get_settings()

# «В работе» и «периодическая эксплуатация» — скважина считается работающей
# (так же определяется работа в верификации обрывов штанг).
WORK_STATUS_CODES: Final = frozenset({"WRK", "PEXP"})
# Открытый интервал в ABAI — dend 3333-12-31; всё позже этого года — «не закрыт».
OPEN_END_YEAR: Final = 3000
NO_FIELD: Final = "—"


def utc_to_local(moment: datetime) -> datetime:
    """Наивный UTC -> наивное местное время."""
    return (
        moment.replace(tzinfo=UTC).astimezone(settings.ZONE_INFO).replace(tzinfo=None)
    )


def local_to_utc(moment: datetime) -> datetime:
    """Наивное местное время -> наивный UTC."""
    return (
        moment.replace(tzinfo=settings.ZONE_INFO).astimezone(UTC).replace(tzinfo=None)
    )


class MrpInputsService:
    def __init__(
        self,
        *,
        repair_repository: RepairRepository,
        status_repository: WellStatusRepository,
        expl_repository: WellExplRepository,
    ) -> None:
        self.repair_repository = repair_repository
        self.status_repository = status_repository
        self.expl_repository = expl_repository

    async def load_fund_inputs(
        self,
        wells: Sequence[Well],
        *,
        window_start: datetime,
        window_end: datetime,
        now: datetime,
    ) -> list[WellFundInput]:
        """Вход расчёта по методике КМГ для скважин за окно [start, end).

        ``window_*`` и ``now`` — наивное местное время.
        """
        if not wells:
            return []
        abai_ids = [well.abai_id for well in wells]

        repairs_by_well: dict[int, list[RepairSpan]] = defaultdict(list)
        # Ремонты, начатые до окна, но идущие в нём, вычитают время работы
        for (
            repair,
            type_name,
        ) in await self.repair_repository.list_with_type_by_well_abai_ids(
            abai_ids,
            since=window_start,
        ):
            if repair.start_time >= window_end:
                continue
            repairs_by_well[repair.abai_well_id].append(
                RepairSpan(
                    id=repair.id,
                    start=repair.start_time,
                    end=repair.end_time,
                    is_gtm=is_gtm_repair(
                        repair_type_id=repair.repair_type_id,
                        repair_type_name=type_name,
                        work_list=repair.work_list,
                    ),
                ),
            )

        # Границы статусов — с запасом в сутки: интервал приводится к местному
        # времени и обрезается окном уже при расчёте.
        status_rows = await self.status_repository.list_intervals_by_abai_well_ids(
            abai_ids,
            since=local_to_utc(window_start) - timedelta(days=1),
            until=local_to_utc(window_end) + timedelta(days=1),
        )
        work_by_well: dict[int, list[Interval]] = defaultdict(list)
        for abai_well_id, dbeg, dend, code, _name, _reason in status_rows:
            if code not in WORK_STATUS_CODES:
                continue
            end = now if dend.year > OPEN_END_YEAR else utc_to_local(dend)
            work_by_well[abai_well_id].append((utc_to_local(dbeg), min(end, now)))

        lift_by_well = await self.expl_repository.get_latest_expl_name_by_abai_well_ids(
            abai_ids,
        )

        return [
            WellFundInput(
                well_id=well.id,
                name=well.name,
                oil_field=well_name_prefix(well.name) or NO_FIELD,
                lift_type=lift_by_well.get(well.abai_id),
                work_intervals=work_by_well.get(well.abai_id, []),
                repairs=repairs_by_well.get(well.abai_id, []),
            )
            for well in wells
        ]
