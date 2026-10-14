"""МРП скважины: до/после каждого ремонта, индикатор качества, среднее."""

from datetime import datetime

from starlette import status

from apps.repairs.dto.internal.mrp import RepairMrpDTO, WellMrpDTO
from apps.repairs.dto.queries.mrp import GetWellMrpQuery
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.services.mrp import RepairSpan, compute_well_mrp, interpret_well_mrp
from apps.repairs.services.repair_classifier import is_gtm_repair
from apps.wells.repositories import WellRepository
from core.settings import get_settings
from shared.errors import HttpError


class WellNotFoundError(HttpError):
    message = "Well not found."
    code = "well_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetWellMrpUseCase:
    def __init__(
        self,
        *,
        repair_repository: RepairRepository,
        well_repository: WellRepository,
    ) -> None:
        self.repair_repository = repair_repository
        self.well_repository = well_repository

    async def execute(
        self,
        query: GetWellMrpQuery,
        *,
        now: datetime | None = None,
        focus_repair_id: int | None = None,
    ) -> WellMrpDTO:
        """``now`` — наивное местное время, как в ремонтах ABAI.

        ``focus_repair_id`` — ремонт, о котором говорит пользователь: в
        интерпретацию добавляется его МРП до/после.
        """
        now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": query.well_id})

        rows = await self.repair_repository.list_with_type_by_well_abai_ids(
            [well.abai_id],
        )
        meta = {
            repair.id: (
                repair,
                type_name,
                is_gtm_repair(
                    repair_type_id=repair.repair_type_id,
                    repair_type_name=type_name,
                    work_list=repair.work_list,
                ),
            )
            for repair, type_name in rows
        }
        mrp = compute_well_mrp(
            [
                RepairSpan(r.id, r.start_time, r.end_time, gtm)
                for r, _name, gtm in meta.values()
            ],
            now=now,
        )

        items = []
        for item in mrp.repairs:
            repair, type_name, gtm = meta[item.repair_id]
            items.append(
                RepairMrpDTO(
                    repair_id=repair.id,
                    start_time=repair.start_time,
                    end_time=repair.end_time,
                    repair_type_name_ru=type_name,
                    is_gtm=gtm,
                    mrp_before_days=_round(item.before_days),
                    mrp_after_days=_round(item.after_days),
                    running_days=_round(item.running_days),
                    verdict=item.verdict,
                    merged_with_adjacent=item.merged,
                ),
            )
        items.sort(key=lambda i: (i.start_time, i.repair_id), reverse=True)

        return WellMrpDTO(
            well_id=well.id,
            well_name=well.name,
            avg_mrp_days=_round(mrp.avg_days),
            intervals_count=mrp.intervals_count,
            repairs_count=mrp.repairs_count,
            worse_count=mrp.worse_count,
            interpretation=interpret_well_mrp(
                well.name,
                mrp,
                repair_id=focus_repair_id,
            ),
            repairs=items,
        )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
