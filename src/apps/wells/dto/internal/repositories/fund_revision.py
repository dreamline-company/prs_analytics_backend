from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateFundRevisionDTO(RepositoryDTO):
    revision_date: date
    well_name: str
    well_id: int | None = None
    ngdu_name: str | None = None
    cdng_name: str | None = None
    lift_type: str | None = None
    pump_depth_m: float | None = None
    hdin_m: float | None = None
    gdis_date: date | None = None
    last_prs_date: date | None = None
    prev_prs_date: date | None = None
    plan_oil: float | None = None
    fact_oil: float | None = None
    fact_liquid: float | None = None
    fact_water_cut: float | None = None
    extra: dict


class UpdateFundRevisionDTO(RepositoryDTO):
    well_id: int | None = None
