"""Ответы SDK «Аргуса» в типизированном виде — без нашей интерпретации.

Числа в ответе бывают строками (``lat``/``lon`` — всегда), пустые словари PHP
отдаёт как ``[]``; разбор здесь только приводит типы, решения (что считать
точкой без фикса, как читать зажигание) — на стороне загрузчика.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


def to_int(value: Any) -> int | None:  # noqa: ANN401
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:  # noqa: ANN401
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> JsonDict:  # noqa: ANN401
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:  # noqa: ANN401
    return value if isinstance(value, list) else []


@dataclass(frozen=True, slots=True)
class ArgusMessageDto:
    """Сообщение трекера (``get_unit_msgs`` или ``lmsg`` в ``get_units``)."""

    time: int  # unix, сек, UTC
    lat: float | None
    lon: float | None
    speed: int | None  # км/ч
    course: int | None  # градусы
    height: int | None  # м
    sats: int | None  # спутников; 0 — нет фикса
    params: str | None  # "ключ:значение;…", набор ключей зависит от трекера

    @classmethod
    def from_payload(cls, payload: JsonDict) -> ArgusMessageDto | None:
        """``None`` — у сообщения нет времени, использовать его нельзя."""
        time = to_int(payload.get("time"))
        if time is None:
            return None
        params = payload.get("params")
        return cls(
            time=time,
            lat=to_float(payload.get("lat")),
            lon=to_float(payload.get("lon")),
            speed=to_int(payload.get("speed")),
            course=to_int(payload.get("course")),
            height=to_int(payload.get("height")),
            sats=to_int(payload.get("sats")),
            params=params if isinstance(params, str) else None,
        )


@dataclass(frozen=True, slots=True)
class ArgusUnitDto:
    """Объект (машина) из ``get_units``.

    ``cf``, ``last_message`` и ``sensors`` приходят только в полном ответе
    (``params={}``); в кратком (``short_details``) они пустые.
    """

    id: int
    name: str
    hw_id: str | None
    hw_type: str | None
    # Карточка машины: ключи с префиксом "cf_" и русскими названиями, набор
    # у машин разный.
    cf: dict[str, str] = field(default_factory=dict)
    last_message: ArgusMessageDto | None = None
    # Описания датчиков объекта (какой параметр трекера что значит).
    sensors: list[Any] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: JsonDict) -> ArgusUnitDto | None:
        """``None`` — у объекта нет id или имени."""
        unit_id = to_int(payload.get("id"))
        name = payload.get("name")
        if unit_id is None or not isinstance(name, str) or not name.strip():
            return None
        hw_id = payload.get("hw_id")
        hw_type = payload.get("hw_type")
        cf = {
            str(key): str(value)
            for key, value in _as_dict(payload.get("cf")).items()
            if value is not None
        }
        lmsg = _as_dict(payload.get("lmsg"))
        return cls(
            id=unit_id,
            name=name.strip(),
            hw_id=str(hw_id) if hw_id not in (None, "") else None,
            hw_type=str(hw_type) if hw_type not in (None, "") else None,
            cf=cf,
            last_message=ArgusMessageDto.from_payload(lmsg) if lmsg else None,
            sensors=_as_list(payload.get("sensors")),
        )
