"""Месторождения в ведомости: разбор фильтра и ключ артефакта — чистая логика.

Месторождение в проекте — буквенный префикс имени скважины (``BLG`` в
``BLG_0177``) из справочника ``oil_fields``. Пользователь называет его именем
или префиксом без учёта регистра; ведомость по подмножеству месторождений —
отдельный артефакт, ключ которого — отсортированные префиксы через запятую.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from apps.org.services.oil_field import well_name_prefix


class OilFieldLike(Protocol):
    id: int
    prefix: str
    name: str


@dataclass(frozen=True, slots=True)
class OilFieldRef:
    id: int
    prefix: str
    name: str


def resolve_oil_fields(
    requested: Iterable[str],
    available: Sequence[OilFieldLike],
) -> tuple[list[OilFieldRef], list[str]]:
    """(найденные месторождения без дублей, неизвестные имена как ввели)."""
    by_key = {}
    for field in available:
        by_key.setdefault(field.prefix.casefold(), field)
        by_key.setdefault(field.name.casefold(), field)
    found: dict[int, OilFieldRef] = {}
    unknown: list[str] = []
    for raw in requested:
        key = raw.strip().casefold()
        if not key:
            continue
        field = by_key.get(key)
        if field is None:
            unknown.append(raw.strip())
        elif field.id not in found:
            found[field.id] = OilFieldRef(
                id=field.id,
                prefix=field.prefix,
                name=field.name,
            )
    return sorted(found.values(), key=lambda f: f.prefix), unknown


def prefixes_key(fields: Sequence[OilFieldRef]) -> str:
    """Ключ артефакта: «BLG,GRN»; пустая строка — весь НГДУ."""
    return ",".join(sorted({field.prefix for field in fields}))


def well_matches(well_name: str, prefixes: Sequence[str]) -> bool:
    """Скважина относится к одному из месторождений фильтра."""
    return well_name_prefix(well_name) in set(prefixes)


def oil_fields_label(fields: Sequence[OilFieldRef]) -> str:
    """Подпись для бланка: «месторождение Прорва (PRV)» или перечисление."""
    if not fields:
        return ""
    parts = [
        field.name if field.name == field.prefix else f"{field.name} ({field.prefix})"
        for field in fields
    ]
    noun = "месторождение" if len(parts) == 1 else "месторождения"
    return f"{noun} {', '.join(parts)}"
