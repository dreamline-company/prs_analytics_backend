"""Матрица инцидентов: у Кайнармунайгаза — только скважины месторождения VMB."""

import asyncio
from types import SimpleNamespace

from apps.detectors.services.well_incident_status import build_well_incident_status
from apps.telemetry.dto.internal.well_rates import WellRatesDTO
from apps.wells.dto.queries.well import GetWellMatrixIncidentsQuery
from apps.wells.use_cases.get_well_matrix_incidents import (
    GetWellMatrixIncidentsUseCase,
)
from shared.constants.ngdu import AbaiNGDUIDsEnum

WELLS = [
    SimpleNamespace(id=1, abai_id=1001, name="VMB_0001"),
    SimpleNamespace(id=2, abai_id=1002, name="UAZ_0002"),
]


class _NgduWells:
    async def list_wells(
        self,
        _ngdu_id: int,
        *,
        oil_field_id: int | None = None,
    ) -> list[SimpleNamespace]:
        del oil_field_id
        return WELLS


class _Orgs:
    def __init__(self, abai_id: int) -> None:
        self.abai_id = abai_id

    async def get_by_id(self, org_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=org_id, abai_id=self.abai_id)


class _Expl:
    async def get_latest_expl_name_by_abai_well_ids(self, _ids: list[int]) -> dict:
        return {}


class _Incidents:
    async def get_for_wells(self, ids: list[int]) -> dict:
        return {well_id: build_well_incident_status([], {}) for well_id in ids}


class _Repairs:
    async def get_for_wells(self, _ids: list[int]) -> dict:
        return {}


class _Rates:
    async def get_for_wells(self, abai_id_by_well_id: dict[int, int]) -> dict:
        empty = dict.fromkeys(WellRatesDTO.model_fields)
        return {well_id: WellRatesDTO(**empty) for well_id in abai_id_by_well_id}


class _FcData:
    async def get_last_savetime_by_well_ids(self, _ids: list[int]) -> dict:
        return {}

    async def get_last_vlt_status_by_well_ids(self, _ids: list[int]) -> dict:
        return {}


def _well_names(abai_ngdu_id: int) -> list[str]:
    use_case = GetWellMatrixIncidentsUseCase(
        ngdu_wells_service=_NgduWells(),
        org_repository=_Orgs(abai_ngdu_id),
        well_expl_repository=_Expl(),
        well_incident_status_service=_Incidents(),
        current_repair_service=_Repairs(),
        well_rates_service=_Rates(),
        sdmo_fc_data_repository=_FcData(),
    )
    rows = asyncio.run(use_case.execute(GetWellMatrixIncidentsQuery(ngdu_id=5)))
    return [row.well.well_name for row in rows]


def test_kainar_matrix_keeps_only_vmb_wells() -> None:
    assert _well_names(AbaiNGDUIDsEnum.KMG) == ["VMB_0001"]


def test_other_ngdu_matrix_is_not_filtered() -> None:
    assert _well_names(AbaiNGDUIDsEnum.ZHMG) == ["UAZ_0002", "VMB_0001"]
