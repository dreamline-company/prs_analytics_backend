from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class DynamogramGdisDTO(BaseModel):
    """Параметры исследования ГДИС в день динамограммы.

    Значения — как в ABAI, без пересчёта единиц; коэффициенты — в долях
    единицы. Поле ``None`` — в исследовании этой метрики нет.
    """

    meas_date: date
    stroke_length_m: float | None = None
    strokes_per_min: float | None = None
    load_max: float | None = None
    load_min: float | None = None
    fill_coef: float | None = None
    pump_feed_coef: float | None = None
    operator_conclusion: str | None = None


class WellDynamogramAIDTO(BaseModel):
    """ИИ-разбор динамограммы для экрана «Интерпретация ИИ».

    Поля разбора заполнены у ``status == "completed"``. У разборов старых
    версий промпта (v2) нет ``state``/``confidence``/наблюдений/рекомендаций и
    источников цифр — эти поля ``None`` или пустые.
    """

    status: str
    prompt_version: str | None = None
    model_name: str | None = None
    processed_at: datetime | None = None
    error: str | None = None

    state: str | None = None
    condition: str | None = None
    # ok / warning / critical / unknown — цвет статуса на экране.
    severity: str | None = None
    confidence: float | None = None
    score: float | None = None
    verdict: str | None = None
    fill_pct: float | None = None
    # gdis — из исследования ГДИС, model — оценка модели по форме кривой.
    fill_source: str | None = None
    pump_efficiency_pct: float | None = None
    pump_efficiency_source: str | None = None
    observations: list[str] = []
    recommendations: list[str] = []
    issues: list[str] = []
    gdis: DynamogramGdisDTO | None = None

    @classmethod
    def from_result(  # noqa: PLR0913
        cls,
        *,
        status: str,
        prompt_version: str | None,
        model_name: str | None,
        processed_at: datetime | None,
        error: str | None,
        result: dict[str, Any] | None,
    ) -> "WellDynamogramAIDTO":
        parsed = (result or {}).get("parsed")
        parsed = parsed if isinstance(parsed, dict) else {}
        gdis = parsed.get("gdis")
        return cls(
            status=status,
            prompt_version=prompt_version,
            model_name=model_name,
            processed_at=processed_at,
            error=error,
            state=_text(parsed.get("state")),
            condition=_text(parsed.get("condition")),
            severity=_text(parsed.get("severity")),
            confidence=_number(parsed.get("confidence")),
            score=_number(parsed.get("score")),
            verdict=_text(parsed.get("verdict")),
            fill_pct=_number(parsed.get("fill_pct")),
            fill_source=_text(parsed.get("fill_source")),
            pump_efficiency_pct=_number(parsed.get("pump_efficiency_pct")),
            pump_efficiency_source=_text(parsed.get("pump_efficiency_source")),
            observations=_texts(parsed.get("observations")),
            recommendations=_texts(parsed.get("recommendations")),
            issues=_texts(parsed.get("issues")),
            gdis=DynamogramGdisDTO.model_validate(gdis)
            if isinstance(gdis, dict)
            else None,
        )


class WellDynamogramDTO(BaseModel):
    """Динамограмма скважины со ссылкой на файл в S3.

    Ссылка временная (presigned): фронт тянет картинку из хранилища напрямую,
    не проксируя через сервис. Если запись файла потерялась, остаются только
    ``id``/``snapshot_time`` — сама динамограмма при этом не скрывается, чтобы
    дырка в хранилище была видна, а не выглядела как отсутствие замера.

    ``ai`` — ИИ-разбор; ``None``, пока динамограмму не разбирали.
    """

    id: int
    well_id: int
    snapshot_time: datetime
    file_id: int
    file_key: str | None = None
    download_url: str | None = None
    expires_at: datetime | None = None
    ai: WellDynamogramAIDTO | None = None


# Разборы v2 хранили числа то строкой ("75.5"), то числом.
def _number(value: Any) -> float | None:  # noqa: ANN401
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return None
    return None


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
