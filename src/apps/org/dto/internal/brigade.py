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
    cdng: str | None
    ngdu_id: int
    fio: str | None
    lift: str | None
    field: str | None
    device: str | None
    violations_count: int = 0
    is_in_repair: bool = False
