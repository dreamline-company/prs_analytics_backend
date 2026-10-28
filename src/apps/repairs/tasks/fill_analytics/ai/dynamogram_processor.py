"""LangGraph processor for a single dynamogram.

Разбор — свойство самой динамограммы, а не ремонта: один и тот же результат
читают аналитика ремонта (пары до/после, KPI «КПД насоса после ПРС»), экран
динамограмм скважины и чат-ассистент. Поэтому роли ДО/ПОСЛЕ в промпте нет.

v3 — надмножество v2: ключи ``pump_efficiency_pct``, ``fill_pct``,
``condition``, ``score``, ``issues``, ``verdict`` сохраняют смысл (их читают
KPI, общий вердикт и экран ремонта), добавлены ``state``, ``severity``,
``confidence``, ``observations``, ``recommendations``, источники цифр и
параметры ГДИС. Наполнение и КПД берутся из ГДИС, если исследование за день
их содержит; оценка модели по картинке — только запасной вариант.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any, Final

from langchain.messages import HumanMessage

from apps.repairs.models.analytics import AI_STATUS_COMPLETED, AI_STATUS_FAILED
from apps.wells.dto.internal.dynamogram import DynamogramGdisDTO
from apps.wells.models.dynamogram import Dynamogram

from .base import AIProcessingResult, BaseAIProcessor

SOURCE_GDIS = "gdis"
SOURCE_MODEL = "model"

# state → (название на экране, severity). Набор — по заключениям операторов
# в ГДИС («НРН», «Влияние газа», «Утечка наг.клапан», …).
DYNAMOGRAM_STATES: Final[dict[str, tuple[str, str]]] = {
    "normal": ("Норма", "ok"),
    "underfill": ("Недостаточный приток", "warning"),
    "gas": ("Влияние газа", "warning"),
    "discharge_valve_leak": ("Утечка нагнетательного клапана", "critical"),
    "suction_valve_leak": ("Утечка приёмного клапана", "critical"),
    "both_valves_leak": ("Утечка в обоих клапанах", "critical"),
    "plunger_tap": ("Удар плунжера", "warning"),
    "plunger_stuck": ("Прихват плунжера", "critical"),
    "rod_break": ("Обрыв / отворот штанг", "critical"),
    "sand_paraffin": ("Влияние песка / АСПО", "warning"),
    "other": ("Другое отклонение", "warning"),
    "unreadable": ("Динамограмма не читается", "unknown"),
}
_STATE_HINTS: Final[dict[str, str]] = {
    "normal": "нормальная работа насоса",
    "underfill": "неполное заполнение насоса жидкостью, недостаточный приток",
    "gas": "влияние газа на работу насоса",
    "discharge_valve_leak": "утечка в нагнетательном клапане",
    "suction_valve_leak": "утечка в приёмном (всасывающем) клапане",
    "both_valves_leak": "утечка в обоих клапанах",
    "plunger_tap": "удар плунжера вверху или внизу",
    "plunger_stuck": "прихват или заклинивание плунжера",
    "rod_break": "обрыв или отворот штанг",
    "sand_paraffin": "влияние песка или АСПО",
    "other": "другое отклонение, не из списка",
    "unreadable": "на изображении не динамограмма или она не читается",
}

# Коэффициенты ГДИС — в долях; часть записей внесена сразу в процентах. На
# проде (27.10.2026) значения 1.2–5 — ни доли, ни проценты (3,6 тыс. у K
# подачи), а ноль — почти всегда заглушка «не измерено» (9,8 тыс. нулей
# K подачи, заключение оператора есть у 213 из них). Доли 1.0–1.2 — 100 %:
# КПД и наполнение выше 100 % экран и KPI не показывают.
_MAX_COEF_FRACTION = 1.2
_MIN_COEF_PERCENT = 5
_MAX_PCT = 100.0

_OUTPUT_SCHEMA_HINT = {
    "state": "строка — ровно один код состояния из списка",
    "confidence": "число 0..100 — насколько уверенно форма кривой определяет состояние",
    "score": "число 0..100 — общая оценка состояния насоса (100 — идеальная работа)",
    "fill_pct_estimate": "число 0..100 — твоя оценка наполнения насоса по форме кривой",
    "pump_efficiency_pct_estimate": (
        "число 0..100 — твоя оценка коэффициента подачи (КПД) по форме кривой"
    ),
    "observations": ["строки — 2–5 признаков формы кривой, на которых основан вывод"],
    "issues": ["строки — обнаруженные дефекты; при норме — пустой список"],
    "recommendations": ["строки — 1–3 практических действия для технолога"],
    "verdict": "строка — 1–2 предложения вывода по-русски",
}


@dataclass(slots=True)
class DynamogramProcessingInput:
    dynamogram: Dynamogram
    s3_key: str
    image_bytes: bytes
    image_mime: str = "image/png"
    gdis: DynamogramGdisDTO | None = None


class DynamogramAIProcessor(BaseAIProcessor[DynamogramProcessingInput]):
    prompt_version: str = "v3"

    async def process(self, item: DynamogramProcessingInput) -> AIProcessingResult:
        result = await super().process(item)
        if result.status != AI_STATUS_COMPLETED:
            return result
        payload = result.result or {}
        parsed = payload.get("parsed")
        if not isinstance(parsed, dict):
            # Без JSON экрану показывать нечего; сырой ответ — для разбора.
            result.status = AI_STATUS_FAILED
            result.error = "Ответ модели — не JSON-объект"
            return result
        if (
            _recognized_state(parsed.get("state")) is None
            and _text(parsed.get("verdict")) is None
        ):
            # JSON не по схеме ({"error": ...}, ответ по схеме v2): сохранить
            # его как «Другое отклонение» без вывода — навсегда закрепить мусор.
            result.status = AI_STATUS_FAILED
            result.error = "Ответ модели не по схеме: нет state и verdict"
            return result
        result.result = {
            "raw": payload.get("raw"),
            "parsed": build_parsed(parsed, item.gdis),
        }
        return result

    def _build_state(self, item: DynamogramProcessingInput) -> dict[str, Any]:
        prompt = build_prompt(item)
        b64 = base64.b64encode(item.image_bytes).decode("ascii")
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{item.image_mime};base64,{b64}"},
            },
        ]
        return {"messages": [HumanMessage(content=content)]}


def build_prompt(item: DynamogramProcessingInput) -> str:
    snapshot = item.dynamogram.snapshot_time.date().strftime("%d.%m.%Y")
    states = "\n".join(f"- {code} — {hint}" for code, hint in _STATE_HINTS.items())
    return (
        "Ты — инженер-технолог по механизированной добыче (ШГН). На изображении "
        f"поверхностная динамограмма скважины, снятая {snapshot}: нагрузка на "
        "полированный шток в зависимости от хода точки подвеса.\n\n"
        f"{_gdis_block(item.gdis)}\n\n"
        "Определи состояние глубинного насоса по форме кривой. Коды состояний:\n"
        f"{states}\n\n"
        "Правила:\n"
        "- state — ровно один код из списка; при признаках нескольких состояний "
        "выбери главное, остальное перечисли в issues и снизь confidence.\n"
        "- Наполнение и КПД: если они даны в параметрах ГДИС, в verdict называй "
        "именно их; свои оценки fill_pct_estimate и pump_efficiency_pct_estimate "
        "всё равно поставь по форме кривой. Если в ГДИС их нет — назови в "
        "verdict свою оценку и прямо скажи, что это оценка по форме кривой.\n"
        "- observations — что именно видно на кривой (линии хода, углы петли, "
        "нагрузка в начале и конце хода, разгрузка), без общих слов.\n"
        "- Если на изображении не динамограмма или её не прочитать — state "
        '"unreadable", оценки null, confidence низкая.\n\n'
        "Верни СТРОГО валидный JSON — без пояснений и без markdown-обёртки. Все "
        "поля обязательны; если значение оценить нельзя — null (для списков — "
        "пустой список).\n\n"
        "Схема ответа:\n"
        f"{json.dumps(_OUTPUT_SCHEMA_HINT, ensure_ascii=False)}\n"
    )


def _gdis_block(gdis: DynamogramGdisDTO | None) -> str:
    if gdis is None:
        return "Исследования ГДИС за этот день нет — параметров подвески нет."
    lines = [
        ("Длина хода, м", _fmt(gdis.stroke_length_m)),
        ("Число качаний, 1/мин", _fmt(gdis.strokes_per_min)),
        ("P максимальной нагрузки", _fmt(gdis.load_max)),
        ("P минимальной нагрузки", _fmt(gdis.load_min)),
        ("Наполнение (K наполнения)", _fmt_pct(coef_to_pct(gdis.fill_coef))),
        ("КПД (K подачи насоса)", _fmt_pct(coef_to_pct(gdis.pump_feed_coef))),
    ]
    present = [f"- {name}: {value}" for name, value in lines if value is not None]
    if not present:
        return "В исследовании ГДИС за этот день параметров подвески нет."
    return "Параметры исследования ГДИС за этот день (из ABAI):\n" + "\n".join(
        present,
    )


def build_parsed(
    model_output: dict[str, Any],
    gdis: DynamogramGdisDTO | None,
) -> dict[str, Any]:
    """Ответ модели → результат v3: проверка, нормализация, цифры ГДИС."""
    state = _state(model_output.get("state"))
    label, severity = DYNAMOGRAM_STATES[state]
    fill_estimate = _pct(model_output.get("fill_pct_estimate"))
    kpd_estimate = _pct(model_output.get("pump_efficiency_pct_estimate"))
    fill_pct, fill_source = _pick(
        coef_to_pct(gdis.fill_coef) if gdis else None,
        fill_estimate,
    )
    kpd_pct, kpd_source = _pick(
        coef_to_pct(gdis.pump_feed_coef) if gdis else None,
        kpd_estimate,
    )
    return {
        "state": state,
        "condition": label,
        "severity": severity,
        "confidence": _pct(model_output.get("confidence")),
        "score": _pct(model_output.get("score")),
        "verdict": _text(model_output.get("verdict")),
        "fill_pct": fill_pct,
        "fill_source": fill_source,
        "fill_pct_estimate": fill_estimate,
        "pump_efficiency_pct": kpd_pct,
        "pump_efficiency_source": kpd_source,
        "pump_efficiency_pct_estimate": kpd_estimate,
        "observations": _texts(model_output.get("observations")),
        "issues": _texts(model_output.get("issues")),
        "recommendations": _texts(model_output.get("recommendations")),
        "gdis": gdis.model_dump(mode="json") if gdis else None,
    }


def coef_to_pct(value: float | None) -> float | None:
    """Коэффициент ГДИС → проценты; ``None`` — значения нет или оно мусорное
    (ноль-заглушка, ни доля, ни процент), тогда в ход идёт оценка модели."""
    if value is None or value <= 0:
        return None
    if value <= _MAX_COEF_FRACTION:
        return min(round(value * 100, 2), _MAX_PCT)
    if _MIN_COEF_PERCENT < value <= _MAX_PCT:
        return round(value, 2)
    return None


def _pick(
    gdis_value: float | None,
    model_value: float | None,
) -> tuple[float | None, str | None]:
    if gdis_value is not None:
        return gdis_value, SOURCE_GDIS
    if model_value is not None:
        return model_value, SOURCE_MODEL
    return None, None


def _recognized_state(value: Any) -> str | None:  # noqa: ANN401
    code = value.strip().lower() if isinstance(value, str) else ""
    return code if code in DYNAMOGRAM_STATES else None


def _state(value: Any) -> str:  # noqa: ANN401
    return _recognized_state(value) or "other"


def _pct(value: Any) -> float | None:  # noqa: ANN401
    """Число 0..100; модель иногда отдаёт его строкой ("75.5")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().rstrip("%").replace(",", "."))
        except ValueError:
            return None
    if not isinstance(value, int | float):
        return None
    number = float(value)
    return number if 0 <= number <= 100 else None  # noqa: PLR2004


def _text(value: Any) -> str | None:  # noqa: ANN401
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _texts(value: Any) -> list[str]:  # noqa: ANN401
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _fmt(value: float | None) -> str | None:
    return None if value is None else f"{value:g}"


def _fmt_pct(value: float | None) -> str | None:
    return None if value is None else f"{value:g} %"
