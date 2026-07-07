from pydantic import BaseModel, ConfigDict


class BrigadeDTO(BaseModel):
    """Brigade view with placeholder analytics fields.

    ``violations_count`` and ``is_in_repair`` are exposed now for the frontend
    contract but not yet computed — kept as defaults until the counter and
    in-repair detection are implemented.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ngdu_id: int
    cdng: str | None = None
    fio: str | None = None
    lift: str | None = None
    field: str | None = None
    device: str | None = None
    violations_count: int = 0
    is_in_repair: bool = False
