"""Госномера машин: Аргус ↔ путёвки УТО ↔ сводки ПРС.

В Аргусе номер записан как ``730AU06`` (бывает суффикс « ВО»), в сводках —
как набрал мастер: кириллицей, без региона, номера на «E» в другом порядке.
Номер Аргуса приводится к одному каноническому виду (``canonical_plate``), а
номер из наших данных даёт несколько кандидатов (``plate_candidates``) — сверка
по пересечению. Правила проверены на данных 14.10.2026 (``docs/argus_api.md``):
путёвки УТО — 44 из 44, сводки — 52 из 59.
"""

import re
from collections.abc import Iterable

# Регион по умолчанию, если в сводке его не дописали: Атырауская область.
DEFAULT_REGION = "06"

# Кириллические буквы, совпадающие по начертанию с латинскими номерного знака.
_CYRILLIC_TO_LATIN = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
# Служебный суффикс в имени объекта Аргуса: "010IZO06 ВО".
_SUFFIX_RE = re.compile(r"\s+BO$")
_NON_ALNUM_RE = re.compile(r"[^0-9A-Z]")
# Номера на «E»: E232ALD (Аргус), E220ALD06 (сводка) и ALD220E (Аргус) — один
# номер в разной записи; канонический вид — E + цифры + буквы, без региона.
_E_FRONT_RE = re.compile(r"E(\d{3})([A-Z]{3})(\d{2})?")
_E_BACK_RE = re.compile(r"([A-Z]{3})(\d{3})E")
_NO_REGION_RE = re.compile(r"\d{3}[A-Z]{2,3}")
_NO_REGION_THREE_LETTERS_RE = re.compile(r"\d{3}[A-Z]{3}")
# Несколько машин в одном поле сводки.
_VEHICLES_SPLIT_RE = re.compile(r"[,;/\n]+|\s+и\s+")


def normalize_plate(raw: str | None) -> str | None:
    """Верхний регистр, кириллица → латиница, без суффикса « ВО», пробелов и
    дефисов. ``None`` — пусто."""
    if not raw:
        return None
    value = raw.strip().upper().translate(_CYRILLIC_TO_LATIN)
    value = _SUFFIX_RE.sub("", value)
    value = _NON_ALNUM_RE.sub("", value)
    return value or None


def canonical_plate(raw: str | None) -> str | None:
    """Канонический вид номера — тот, что хранится в ``gps_unit.plate_normalized``."""
    value = normalize_plate(raw)
    if value is None:
        return None
    if match := _E_FRONT_RE.fullmatch(value):
        return f"E{match[1]}{match[2]}"
    if match := _E_BACK_RE.fullmatch(value):
        return f"E{match[2]}{match[1]}"
    return value


def plate_candidates(raw: str | None) -> set[str]:
    """Канонические номера, которыми может оказаться номер из сводки/путёвки.

    ``720AU`` → ``720AU06`` (регион не дописали); ``237ALD`` → и ``237ALD06``,
    и ``E237ALD`` (номер на «E» без буквы).
    """
    base = canonical_plate(raw)
    if base is None:
        return set()
    candidates = {base}
    if _NO_REGION_RE.fullmatch(base):
        candidates.add(base + DEFAULT_REGION)
    if _NO_REGION_THREE_LETTERS_RE.fullmatch(base):
        candidates.add(f"E{base}")
    return candidates


def split_vehicles(raw: str | None) -> list[str]:
    """Поле «машина» сводки может перечислять несколько машин."""
    if not raw:
        return []
    return [part.strip() for part in _VEHICLES_SPLIT_RE.split(raw) if part.strip()]


def wanted_plates(raw_values: Iterable[str | None]) -> set[str]:
    """Все канонические кандидаты для номеров из наших данных."""
    wanted: set[str] = set()
    for raw in raw_values:
        for vehicle in split_vehicles(raw):
            wanted |= plate_candidates(vehicle)
    return wanted
