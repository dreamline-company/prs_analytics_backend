from pydantic import BaseModel, create_model


def pick_fields(
    model: type[BaseModel],
    name: str,
    fields: list[str],
    *,
    module: str | None = None,
) -> BaseModel:
    selected_fields = {}

    for field in fields:
        model_field = model.model_fields[field]

        selected_fields[field] = (
            model_field.annotation,
            model_field.default if not model_field.is_required() else ...,
        )

    return create_model(name, __module__=module or model.__module__, **selected_fields)
