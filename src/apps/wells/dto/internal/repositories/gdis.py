from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateGdisMetricDTO(RepositoryDTO):
    abai_id: int
    name_ru: str
    name_short_ru: str | None = None
    code: str | None = None
    data_type: int | None = None
    parent_abai_id: int | None = None
    dict_table: str | None = None
    value_double_min: float | None = None
    value_double_max: float | None = None


class UpdateGdisMetricDTO(RepositoryDTO):
    name_ru: str | None = None
    name_short_ru: str | None = None
    code: str | None = None
    data_type: int | None = None
    parent_abai_id: int | None = None
    dict_table: str | None = None
    value_double_min: float | None = None
    value_double_max: float | None = None


class CreateGdisCurrentDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    meas_date: date
    reason: int | None = None
    reason_txt: str | None = None
    device: int | None = None
    target: str | None = None
    note: str | None = None
    transcript_dynamogram: str | None = None
    conclusion: int | None = None
    conclusion_arr: list[int] | None = None
    conclusion_text: str | None = None


class UpdateGdisCurrentDTO(RepositoryDTO):
    abai_well_id: int | None = None
    meas_date: date | None = None
    reason: int | None = None
    reason_txt: str | None = None
    device: int | None = None
    target: str | None = None
    note: str | None = None
    transcript_dynamogram: str | None = None
    conclusion: int | None = None
    conclusion_arr: list[int] | None = None
    conclusion_text: str | None = None


class CreateGdisCurrentValueDTO(RepositoryDTO):
    abai_id: int
    gdis_current_abai_id: int
    metric_abai_id: int
    value_double: float | None = None
    value_string: str | None = None


class UpdateGdisCurrentValueDTO(RepositoryDTO):
    gdis_current_abai_id: int | None = None
    metric_abai_id: int | None = None
    value_double: float | None = None
    value_string: str | None = None
