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
from apps.detectors.services.daily_sheet.builder import DailySheetBuilder
from apps.detectors.services.daily_sheet.config import (
    CATEGORY_HIGH,
    CATEGORY_LOW,
    CATEGORY_MID,
    SEVERITY_MARKED,
    SEVERITY_MODERATE,
    SEVERITY_STRONG,
    sheet_applies,
)
from apps.detectors.services.daily_sheet.context import (
    RateSnapshot,
    RepairInfo,
    WellContext,
)
from apps.detectors.services.daily_sheet.metrics import ratio
from apps.detectors.services.daily_sheet.oil_fields import (
    oil_fields_label,
    prefixes_key,
    resolve_oil_fields,
    well_matches,
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


def test_ratio() -> None:
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
    assert "период 27.08 – 28.08; повторно" in text
    assert texts.status_text(ctx) == "Завершён 29.08"
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
    ctx = _context(incident)
    text = texts.deviation_text(ctx, day_end=_DAY_END, partial_day=True)
    assert "до полной потери нагрузки, сильная" in text
    assert "период 31.08 05:20 – 09:20, по неполным суткам" in text
    assert texts.status_text(ctx) == "Активен · авария"


def test_period_text_active_without_recent_alerts() -> None:
    # Единственные alert-сутки 10.06, эпизод ещё открыт (гистерезис): не «N сут».
    incident = _incident(
        opened_at=datetime(2026, 8, 20),
        last_seen_at=datetime(2026, 8, 21),
        escalated_at=None,
        level="warning",
    )
    ctx = _context(incident)
    text = texts.period_text(ctx, day_end=_DAY_END, partial_day=False)
    assert text == "сутки 20.08, без сработок с 21.08"
    assert texts.status_text(ctx) == "Активен · предупреждение"
    ongoing = _incident(last_seen_at=_DAY_END)
    text = texts.period_text(_context(ongoing), day_end=_DAY_END, partial_day=False)
    assert text == "период 27.08 – 31.08, 5 сут"


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
            RepairInfo(datetime(2026, 8, 24), datetime(2026, 8, 26)),
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


def test_top_text_has_rates_and_rule() -> None:
    ctx = _context(
        _incident(),
        rates=RateSnapshot(
            liquid=19.6,
            oil=0.0,
            plan_liquid=25.0,
            plan_oil=10.46,
            measured_at=_DAY_START - timedelta(days=1),
        ),
    )
    text = texts.top_text(ctx)
    assert text.startswith("UZK_0377 — 60%. Жидкость 19.6 м3/сут при режиме 25")
    assert "нефть 0 т/сут при плане 10.46 (30.08, ТМ)" in text
    assert "K = 0.36 при пороге 0.2, база 58 сут (СДМО)" in text


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
        status_label="Завершён 29.08",
        deviation="недозаполнение насоса, сильная, период 26.08 – 29.08 завершился",
        cause="негерметичность насоса",
        probability_percent=85,
        rates="19.6 / 25",
        plan_oil="10.46",
        incident_ids=[1],
        level="alarm",
        status="normalized",
        severity="strong",
    )
    xml = _document_xml(render_docx(_sheet([row])))
    assert "СУТОЧНАЯ ВЕДОМОСТЬ" in xml
    assert "Статус на дату" in xml
    assert "Завершён 29.08" in xml
    assert "UZK_0377" in xml
    assert "118 из 146" in xml
    assert "w:tblHeader" in xml

    empty = _document_xml(render_docx(_sheet([])))
    assert "не зафиксировано" in empty
    assert "<w:tbl>" not in empty


# --- фильтр по месторождениям --------------------------------------------------


def test_resolve_oil_fields_by_name_or_prefix_case_insensitive() -> None:
    available = [
        SimpleNamespace(id=7, prefix="BLG", name="BLG"),
        SimpleNamespace(id=15, prefix="GRN", name="Гран"),
    ]
    found, unknown = resolve_oil_fields(["grn", " Гран ", "blg", "XXX"], available)
    assert [f.prefix for f in found] == ["BLG", "GRN"]
    assert unknown == ["XXX"]
    assert prefixes_key(found) == "BLG,GRN"
    assert prefixes_key([]) == ""
    assert well_matches("GRN_0012", ["BLG", "GRN"])
    assert not well_matches("UZK_0377", ["BLG", "GRN"])
    assert not well_matches("00001-CT", ["BLG"])
    assert oil_fields_label(found) == "месторождения BLG, Гран (GRN)"
    assert oil_fields_label(found[:1]) == "месторождение BLG"
    assert oil_fields_label([]) == ""


# --- R10: замеры ЦИТС ------------------------------------------------------------


def _r10_incident(**payload) -> SimpleNamespace:  # noqa: ANN003
    base = {
        "abai_ngdu_id": 11,
        "event": 1,
        "klass": "отклонение от техрежима, свежее",
        "q_last": 12.0,
        "rezhim": 30.0,
        "dev": -0.6,
        "n_series": 3,
        "series_from": "2026-08-29",
        "prev_dev": 0.0,
        "su": None,
        "note": "",
    }
    return _incident(
        detector_code="R10",
        reason_code="liquid_loss",
        entity_id=None,
        level="warning",
        escalated_at=None,
        opened_at=datetime(2026, 8, 29),
        last_seen_at=datetime(2026, 9, 1),
        payload={**base, **payload},
    )


def test_r10_sheet_only_for_its_ngdu() -> None:
    assert sheet_applies("R10", 11)
    assert not sheet_applies("R10", 12)
    assert sheet_applies("R9", 12)


def test_r10_severity_by_deviation_and_zeros() -> None:
    assert severity_key("R10", {"event": 2, "dev": None}) == SEVERITY_STRONG
    assert severity_key("R10", {"event": 1, "dev": -0.8}) == SEVERITY_STRONG
    assert severity_key("R10", {"event": 1, "dev": -0.55}) == SEVERITY_MARKED
    assert severity_key("R10", {"event": 1, "dev": -0.35}) == SEVERITY_MODERATE


def test_r10_deviation_and_rule_texts() -> None:
    drop = _context(_r10_incident(), cause="Снижение дебита (R10)")

    assert texts.deviation_text(drop, day_end=_DAY_END, partial_day=False) == (
        "снижение дебита жидкости по замерам ЦИТС (отклонение от техрежима, "
        "свежее), выраженная, период 29.08 – 31.08, 3 сут"
    )
    assert (
        "Qж 12 при техрежиме 30 (-60 %), замеров за порогом подряд: 3 с 29.08; "
        "до серии +0 % (ЦИТС)"
    ) in texts.top_text(drop)

    zeros = _context(
        _r10_incident(
            event=2,
            klass="нулевые замеры, СУ работает",
            dev=None,
            q_last=0.0,
            n_series=2,
            series_from="2026-08-30",
            su="СУ работает",
            note="доля работы СУ в дни нулей: 100%, 100%",
        ),
    )
    assert (
        "нулевых замеров подряд: 2 с 30.08, техрежим 30; СУ работает; "
        "доля работы СУ в дни нулей: 100%, 100% (ЦИТС)"
    ) in texts.top_text(zeros)


def test_r10_measure_request_text() -> None:
    payload = {
        "last_day": "2026-08-03",
        "age_d": 28,
        "su": "СУ стоит, статуса простоя нет",
        "note": "периодическая эксплуатация",
    }

    assert texts.measure_request_text("UVK_0424", payload) == (
        "UVK_0424 — последний замер 03.08 (28 сут назад); "
        "СУ стоит, статуса простоя нет; периодическая эксплуатация"
    )


def test_render_r10_sheet_with_cits_coverage_and_measure_requests() -> None:
    sheet = _sheet([]).model_copy(
        update={
            "detector_code": "R10",
            "detector_name_ru": "Снижение дебита по замерам ЦИТС",
            "coverage": DailySheetCoverageDTO(
                source="cits",
                stations_total=630,
                stations_reporting=400,
                stations_processed=630,
            ),
            "measure_requests": ["UVK_0424 — последний замер 03.08 (56 сут назад)"],
        },
    )

    xml = _document_xml(render_docx(sheet))

    assert "по замерам ЦИТС относительно техрежима" in xml
    assert "замеры ЦИТС за сутки есть у 400 из 630 скважин" in xml
    assert "сутки правилом обработаны" in xml
    assert "Запросить замер" in xml
    assert "UVK_0424" in xml


def test_active_rows_before_closed() -> None:
    # Завершённый эпизод с большей вероятностью — всё равно под активным.
    closed = _context(
        _incident(id=1, normalized_at=datetime(2026, 8, 30)),
        confidence=0.9,
    )
    active = _context(
        _incident(id=2, well_id=11, last_seen_at=_DAY_END),
        well_id=11,
        well_name="UZK_0378",
        confidence=0.4,
    )
    builder = DailySheetBuilder.__new__(DailySheetBuilder)
    builder.now = _DAY_END
    target = SimpleNamespace(
        detector_code="R9",
        ngdu_id=5,
        ngdu_name="Кайнармунайгаз",
        abai_ngdu_id=12,
        sheet_date=_DAY,
        oil_fields=[],
    )
    coverage = DailySheetCoverageDTO(
        stations_total=1,
        stations_reporting=1,
        stations_processed=1,
    )

    sheet = builder._compose(  # noqa: SLF001
        target,  # type: ignore[arg-type]
        "Перекос нагрузки",
        [closed, active],
        coverage,
        day_end=_DAY_END,
    )

    assert [(r.well_name, r.status_label) for r in sheet.rows] == [
        ("UZK_0378", "Активен · авария"),
        ("UZK_0377", "Завершён 29.08"),
    ]
