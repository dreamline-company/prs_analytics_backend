"""Месторождение как буквенный префикс имени скважины.

Справочник ``oil_fields`` строится из имён скважин: ``BLG`` в ``BLG_0177``.
Тот же разбор нужен и при заполнении справочника, и при фильтрации скважин по
месторождению — одно место, чтобы правила не разъехались.
"""

import re
from collections.abc import Iterable
from typing import Protocol

# Буквенный префикс до подчёркивания: BLG_0177 -> BLG. Имена вида 00001-CT
# префикса не имеют и ни к какому месторождению не относятся.
WELL_NAME_PREFIX_RE = re.compile(r"^([A-Za-z]+)_")


class _HasName(Protocol):
    name: str


def well_name_prefix(well_name: str) -> str | None:
    match = WELL_NAME_PREFIX_RE.match(well_name)
    return match.group(1) if match else None


def wells_with_prefix[WellT: _HasName](
    wells: Iterable[WellT],
    prefix: str,
) -> list[WellT]:
    """Скважины месторождения — те, чей префикс имени совпадает с ``prefix``."""
    return [well for well in wells if well_name_prefix(well.name) == prefix]
