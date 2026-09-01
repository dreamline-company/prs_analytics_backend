from pydantic import BaseModel, ConfigDict, Field


class ConclusionFeedbackRequestDTO(BaseModel):
    """Оценка заключения: звёзды 1–5 + обязательный комментарий."""

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    comment: str = Field(min_length=3, max_length=4000)
