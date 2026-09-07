from pydantic import BaseModel, ConfigDict, Field


class GetNgduSummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Оба фильтра необязательны: без них — все НГДУ. Месторождение из
    # GET /org/v1/oil-fields само определяет НГДУ; если задан и ngdu_id, они
    # должны совпадать.
    ngdu_id: int | None = Field(default=None, ge=1)
    oil_field_id: int | None = Field(default=None, ge=1)
