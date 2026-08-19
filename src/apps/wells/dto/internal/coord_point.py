from pydantic import BaseModel

# Значения поля ``issue``.
COORD_ISSUE_EMPTY = "empty"  # координата (0, 0)
COORD_ISSUE_CRS_MISMATCH = "crs_mismatch"  # система не соответствует значениям
COORD_ISSUE_UNKNOWN_CRS = "unknown_crs"  # система координат не указана
COORD_ISSUE_TRANSFORM_FAILED = "transform_failed"  # ST_Transform не смог
COORD_ISSUE_OUT_OF_RANGE = "out_of_range"  # результат вне допустимых широт/долгот


class WellCoordPointDTO(BaseModel):
    """Координата устья скважины для отрисовки на карте.

    ``lat``/``lon`` — WGS 84, уже пригодны для карты: значения из
    географической системы отдаются как есть, из проекционной —
    перепроецируются. Если координату отрисовать нельзя, они пустые,
    ``is_mappable`` = false, а причина лежит в ``issue``; исходные ``x``/``y``
    и система при этом отдаются всегда — чтобы было что показать в паспорте.
    """

    lat: float | None
    lon: float | None
    is_mappable: bool
    issue: str | None
    x: float | None
    y: float | None
    srid: int | None
    system: str | None
    system_name: str | None
