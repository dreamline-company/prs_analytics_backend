from pydantic import BaseModel


class AppResponse[D](BaseModel):
    """Generic successful API response wrapper.

    Use this DTO when API endpoints should return data under a common
    top-level ``data`` field.

    Example:
        AppResponse[UserReadDTO]
        AppResponse[list[UserReadDTO]]
    """

    data: D


class PaginationMeta(BaseModel):
    """Pagination metadata."""

    page: int
    page_size: int
    total: int
    pages: int


class PaginationResponse[D](BaseModel):
    """Generic paginated API response wrapper."""

    data: list[D]
    meta: PaginationMeta


class ErrorResponse(BaseModel):
    """Generic error API response wrapper."""

    code: str
    message: str
    details: dict | None = None
