from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DonorStatus = Literal["pending", "accepted", "applied", "rejected", "reserve"]


class GetCompensationContourQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)


class ListCompensationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngdu_id: int = Field(ge=1)
    # Месторождение из /org/v1/oil-fields; должно относиться к ngdu_id.
    oil_field_id: int | None = Field(default=None, ge=1)


class ListCompensationRecommendationsQuery(ListCompensationQuery):
    limit: int = Field(default=3, ge=1, le=100)


class ListCompensationDonorsQuery(ListCompensationQuery):
    status: DonorStatus | None = None
