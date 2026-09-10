"""Чистая логика суточной ведомости: отбор на дату, сила, категория, тексты, рендер."""

# ruff: noqa: DTZ001 — метки эпизодов и телеметрии наивные, как в БД

import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

from apps.detectors.dto.internal.daily_sheet import (
    DailySheetCoverageDTO,
    DailySheetDTO,
    DailySheetRowDTO,
    DailySheetTopItemDTO,
)
from apps.detectors.services.daily_sheet import texts
from apps.detectors.services.daily_sheet.config import (
    CATEGORY_HIGH,
    CATEGORY_LOW,
    CATEGORY_MID,
    SEVERITY_MARKED,
    SEVERITY_MODERATE,
    SEVERITY_STRONG,
)
from apps.detectors.services.daily_sheet.context import (
    LevelInfo,
    RateSnapshot,
    RepairInfo,
    WellContext,
)
from apps.detectors.services.daily_sheet.metrics import (
    edge_means,
    median,
    ratio,
    window_medians,
)
from apps.detectors.services.daily_sheet.render import render_docx
from apps.detectors.services.daily_sheet.selection import (
    Episode,
    category_key,
    day_bounds,
    episodes_on_date,
    group_by_well,
    in_scope,
    severity_key,
    state_on_date,
)

# Метки эпизодов и телеметрии наивные — как в БД.
_DAY = date(2026, 8, 31)
_DAY_START, _DAY_END = day_bounds(_DAY)


def _incident(**overrides) -> SimpleNamespace:  # noqa: ANN003
    opened = datetime(2026, 8, 27)
    base = {
        "id": 1,
        "well_id": 10,
        "entity_id": 100,
        "detector_code": "R9",
        "reason_code": "load_imbalance",
        "level": "alarm",
        "status": "active",
        "opened_at": opened,
        "detected_at": opened + timedelta(days=1, hours=4),
        "last_seen_at": datetime(2026, 8, 29),
        "escalated_at": datetime(2026, 8, 28),
        "normalized_at": None,
        "payload": {"k": 0.36, "k_alert": 0.2, "baseline": {"n_days": 58}},
    }
    return SimpleNamespace(**{**base, **overrides})


def _context(incident: SimpleNamespace, **overrides) -> WellContext:  # noqa: ANN003
    status, level = state_on_date(incident, day_end=_DAY_END)
    fields = {
        "detector_code": incident.detector_code,
        "well_id": incident.well_id,
        "well_name": "UZK_0377",
        "episodes": [Episode(incident=incident, status=status, level=level)],
        "confidence": 0.62,
        "cause": "Потеря полезной нагрузки (R9)",
        "recommendations": [
            {
                "step": 1,
                "text": "Динамометрирование",
                "role": "Технолог",
                "deadline_hours": 48,
            },
        ],
    }
    return WellContext(**{**fields, **overrides})


# --- отбор и состояние на дату -------------------------------------------------


def test_day_bounds_are_right_open() -> None:
    assert datetime(2026, 8, 31) == _DAY_START
    assert datetime(2026, 9, 1) == _DAY_END


def test_in_scope_active_and_recently_normalized_only() -> None:
    kwargs = {"day_start": _DAY_START, "day_end": _DAY_END, "tail_days": 14}
    assert in_scope(_incident(), **kwargs)
    assert not in_scope(_incident(opened_at=_DAY_END), **kwargs)
    recent = _incident(normalized_at=_DAY_START - timedelta(days=13))
    old = _incident(normalized_at=_DAY_START - timedelta(days=15))
    assert in_scope(recent, **kwargs)
    assert not in_scope(old, **kwargs)


def test_state_on_date_ignores_events_after_the_day() -> None:
    later = _incident(
        normalized_at=_DAY_END + timedelta(hours=2),
        escalated_at=_DAY_END + timedelta(hours=1),
    )
    assert state_on_date(later, day_end=_DAY_END) == ("active", "warning")
    closed = _incident(normalized_at=_DAY_END - timedelta(hours=1))
    assert state_on_date(closed, day_end=_DAY_END) == ("normalized", "alarm")
    # Открыт сразу алармом: escalated_at пуст, уровень берётся как есть.
    assert state_on_date(_incident(escalated_at=None), day_end=_DAY_END)[1] == "alarm"


def test_group_by_well_puts_active_then_newest_first() -> None:
    old_active = _incident(id=1, opened_at=datetime(2026, 8, 20))
    new_closed = _incident(
        id=2,
        opened_at=datetime(2026, 8, 28),
        normalized_at=datetime(2026, 8, 30),
    )
    other = _incident(id=3, well_id=11)
    grouped = group_by_well(
        episodes_on_date([new_closed, other, old_active], sheet_date=_DAY),
    )
    assert [e.incident.id for e in grouped[10]] == [1, 2]
    assert [e.incident.id for e in grouped[11]] == [3]


# --- сила и категория ------------------------------------------------------------


def test_severity_bands() -> None:
    assert severity_key("R9", {"k": 0.5}) == SEVERITY_STRONG
    assert severity_key("R9", {"k": 0.35}) == SEVERITY_MARKED
    assert severity_key("R9", {"k": 0.21}) == SEVERITY_MODERATE
    assert severity_key("R2", {"current_ratio": 0.2}) == SEVERITY_STRONG
    assert severity_key("R2", {"current_ratio": 0.4}) == SEVERITY_MARKED
    assert severity_key("R2", {"current_ratio": 0.55}) == SEVERITY_MODERATE
    assert severity_key("R9", None) == SEVERITY_MODERATE


def test_category_by_plan_oil() -> None:
    assert category_key(None) is None
    assert category_key(1.44) == CATEGORY_LOW
    assert category_key(2.39) == CATEGORY_MID
    assert category_key(7.18) == CATEGORY_MID
    assert category_key(10.46) == CATEGORY_HIGH


# --- метрики -----------------------------------------------------------------------


def test_median_and_windows() -> None:
    assert median([]) is None
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    points = [(_DAY_END - timedelta(days=d, hours=1), float(20 - d)) for d in range(14)]
    recent, previous = window_medians(points, end=_DAY_END, days=7)
    assert (recent, previous) == (17.0, 10.0)
    first, last = edge_means(points, end=_DAY_END, span_days=14, window_days=7)
    assert (first, last) == (10.0, 17.0)
    assert ratio(19.6, 25) == 19.6 / 25
    assert ratio(19.6, 0) is None


# --- тексты граф -------------------------------------------------------------------


def test_span_text_shows_last_alert_day_not_next_midnight() -> None:
    opened = datetime(2026, 8, 26)
    assert (
        texts.span_text(opened, datetime(2026, 8, 27), dates_only=True) == "сутки 26.08"
    )
    assert (
        texts.span_text(opened, datetime(2026, 8, 30), dates_only=True)
        == "период 26.08 – 29.08"
    )
    start = datetime(2026, 8, 25, 18, 20)
    assert texts.span_text(start, datetime(2026, 8, 25, 22, 20), dates_only=False) == (
        "период 25.08 18:20 – 22:20"
    )


def test_deviation_text_for_closed_episode_with_losses() -> None:
    incident = _incident(normalized_at=datetime(2026, 8, 30))
    ctx = _context(
        incident,
        rates=RateSnapshot(liquid=10.0, plan_liquid=25.0, measured_at=_DAY_START),
        previous=[
            _incident(
                id=9,
                opened_at=datetime(2026, 7, 27),
                last_seen_at=datetime(2026, 7, 29),
                normalized_at=datetime(2026, 8, 1),
            ),
        ],
    )
    text = texts.deviation_text(ctx, day_end=_DAY_END, partial_day=False)
    assert text.startswith("недозаполнение насоса")
    assert "выраженная" in text
    assert "период 27.08 – 28.08, эпизод завершён" in text
    assert "повторно: пред. проявление 27.07 – 28.07" in text
    assert text.endswith("потери сохраняются")


def test_deviation_text_r2_total_loss_and_partial_day() -> None:
    incident = _incident(
        detector_code="R2",
        reason_code="rod_break",
        opened_at=datetime(2026, 8, 31, 5, 20),
        last_seen_at=datetime(2026, 8, 31, 9, 20),
        escalated_at=None,
        payload={"current_ratio": 0.2, "base_moment": 48.5},
    )
    text = texts.deviation_text(_context(incident), day_end=_DAY_END, partial_day=True)
    assert "до полной потери нагрузки, сильная" in text
    assert "период 31.08 05:20 – 09:20, продолжается — по неполным суткам" in text


def test_period_text_active_without_recent_alerts() -> None:
    # Единственные alert-сутки 10.06, эпизод ещё открыт (гистерезис): не «N сут».
    incident = _incident(
        opened_at=datetime(2026, 8, 20),
        last_seen_at=datetime(2026, 8, 21),
        escalated_at=None,
        level="warning",
    )
    text = texts.period_text(_context(incident), day_end=_DAY_END, partial_day=False)
    assert text == "сутки 20.08, без сработок с 21.08"
    ongoing = _incident(last_seen_at=_DAY_END)
    text = texts.period_text(_context(ongoing), day_end=_DAY_END, partial_day=False)
    assert text == "период 27.08 – 31.08, 5 сут, продолжается"


def test_cause_text_qualifiers() -> None:
    watered = _context(
        _incident(),
        rates=RateSnapshot(
            liquid=15.0,
            oil=0.0,
            water_cut=100.0,
            plan_liquid=15.0,
            measured_at=_DAY_START,
        ),
    )
    assert texts.cause_text(watered).endswith("обводнение — нефти нет")
    after_repair = _context(
        _incident(),
        repairs=[
            RepairInfo(
                "ТР 4-1",
                "Смена насоса",
                datetime(2026, 8, 24),
                datetime(2026, 8, 26),
            ),
        ],
        rates=RateSnapshot(liquid=30.0, plan_liquid=28.0, measured_at=_DAY_START),
    )
    text = texts.cause_text(after_repair)
    assert "пуск после ремонта 26.08" in text
    assert "потерь добычи пока нет" in text


def test_rates_text_marks_stale_measurements() -> None:
    fresh = _context(
        _incident(),
        rates=RateSnapshot(liquid=19.6, plan_liquid=25.0, measured_at=_DAY_START),
    )
    stale = _context(
        _incident(),
        rates=RateSnapshot(
            liquid=19.6,
            plan_liquid=25.0,
            measured_at=_DAY_START,
            stale=True,
        ),
    )
    assert texts.rates_text(fresh) == "19.6 / 25"
    assert texts.rates_text(stale) == "нет замеров / 25"
    assert (
        texts.plan_oil_text(_context(_incident(), rates=RateSnapshot(plan_oil=10.46)))
        == "10.46"
    )


def test_recommendation_text_has_steps_and_basis() -> None:
    ctx = _context(
        _incident(),
        rates=RateSnapshot(
            liquid=19.6,
            oil=0.0,
            plan_liquid=25.0,
            plan_oil=10.46,
            measured_at=_DAY_START - timedelta(days=1),
            liquid_recent=19.6,
            liquid_previous=30.0,
            water_cut_first=55.0,
            water_cut_last=79.0,
        ),
        level=LevelInfo(value_m=28.0, meas_date=date(2026, 8, 28)),
        ai_summary="x" * 400,
    )
    text = texts.recommendation_text(ctx)
    assert text.startswith("1) Динамометрирование (Технолог, 48 ч). Основание: ")
    assert "K = 0.36 при пороге 0.2, база 58 сут (СДМО)" in text
    assert (
        "жидкость 19.6 м3/сут при режиме 25, нефть 0 т/сут при плане 10.46 (30.08, ТМ)"
        in text
    )
    assert "медианный дебит снизился с 30 до 19.6 м3/сут за неделю" in text
    assert "обводнённость выросла с 55 до 79 % за 30 суток" in text
    assert "ремонтов за 60 суток нет (ABAI)" in text
    assert "замер уровня 28.08 (ABAI) — 28 м" in text
    assert "ИИ-заключение: " + "x" * 299 + "…" in text


def test_probability_rounds_to_five() -> None:
    assert _context(_incident(), confidence=0.62).probability_percent == 60
    assert _context(_incident(), confidence=0.88).probability_percent == 90


def test_fmt_num_strips_zeros() -> None:
    assert texts.fmt_num(25.0) == "25"
    assert texts.fmt_num(10.46, 2) == "10.46"
    assert texts.fmt_num(None) == "—"


# --- рендер -----------------------------------------------------------------------


def _sheet(rows: list[DailySheetRowDTO]) -> DailySheetDTO:
    return DailySheetDTO(
        detector_code="R9",
        detector_name_ru="Перекос нагрузки",
        ngdu_id=5,
        ngdu_name="Кайнармунайгаз",
        abai_ngdu_id=12,
        sheet_date=_DAY,
        status="completed",
        rows_count=len(rows),
        coverage=DailySheetCoverageDTO(
            stations_total=146,
            stations_reporting=118,
            stations_processed=118,
        ),
        built_at=_DAY_END,
        config_version="sheet-v1",
        file_id=None,
        top=[
            DailySheetTopItemDTO(
                rank=1,
                well_name="UZK_0377",
                probability_percent=60,
                text="UZK_0377 — 60%.",
            ),
        ]
        if rows
        else [],
        rows=rows,
    )


def _document_xml(buffer: BytesIO) -> str:
    with zipfile.ZipFile(buffer) as archive:
        return archive.read("word/document.xml").decode()


def test_render_docx_with_rows_and_empty() -> None:
    row = DailySheetRowDTO(
        number=1,
        well_id=10,
        well_name="UZK_0377",
        category="Высокодебитные",
        detected_at=datetime(2026, 8, 26, 9, 14),
        deviation="недозаполнение насоса, сильная, период 26.08 – 29.08 завершился",
        cause="негерметичность насоса",
        probability_percent=85,
        rates="19.6 / 25",
        plan_oil="10.46",
        recommendation="ПРС. Основание: …",
        incident_ids=[1],
        level="alarm",
        status="normalized",
        severity="strong",
    )
    xml = _document_xml(render_docx(_sheet([row])))
    assert "СУТОЧНАЯ ВЕДОМОСТЬ" in xml
    assert "UZK_0377" in xml
    assert "118 из 146" in xml
    assert "w:tblHeader" in xml

    empty = _document_xml(render_docx(_sheet([])))
    assert "не зафиксировано" in empty
    assert "<w:tbl>" not in empty
