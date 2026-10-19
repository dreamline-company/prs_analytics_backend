from pydantic import BaseModel, ConfigDict, Field


class GetMrpSummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Как в сводке НГДУ: оба фильтра необязательны, без них — все НГДУ.
    ngdu_id: int | None = Field(default=None, ge=1)
    oil_field_id: int | None = Field(default=None, ge=1)
    # Последний месяц окна, «YYYY-MM»; по умолчанию — прошлый полный месяц
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    months: int = Field(default=12, ge=1, le=36)
    crf_min_repairs: int = Field(default=3, ge=1)


class GetMrpDynamicsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngdu_id: int | None = Field(default=None, ge=1)
    oil_field_id: int | None = Field(default=None, ge=1)
    # Последний месяц ряда, «YYYY-MM»; по умолчанию — прошлый полный месяц
    end_month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    # Сколько месяцев в ряду
    points: int = Field(default=12, ge=1, le=36)
    # Длина скользящего периода, мес (методика КМГ — 12)
    window_months: int = Field(default=12, ge=1, le=24)
