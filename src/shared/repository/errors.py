from shared.errors import AppError


class FiltersIsNotSetError(AppError):
    message = "Recommended to set filters."
    code = "filters_not_set"
