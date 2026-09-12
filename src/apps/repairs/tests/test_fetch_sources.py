"""Чистая логика добытчиков ремонтов и аналитики: окна, отпечатки, решения."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.repairs.tasks.fetch_sources.candidates import (
    is_measure_closed,
    repair_window,
)
from apps.repairs.tasks.fetch_sources.link_spo import (
    measure_overlaps,
    ngdu_abai_id_by_owner,
)
from apps.repairs.tasks.fetch_sources.triggers import analytics_debounce_key
from apps.repairs.tasks.fill_analytics.fetchers.abai_repair_doc_fetcher import (
    content_digest,
    plan_doc_update,
)
from apps.repairs.tasks.fill_analytics.fetchers.kbrs_spo_fetcher import (
    extract_well_number,
    owner_id_by_ngdu_abai_id,
)
from apps.repairs.tasks.fill_analytics.fetchers.spo_persist import is_up_to_date
from apps.repairs.tasks.fill_analytics.fetchers.uto_transport_fetcher import (
    UtoTransportFetcher,
)
from apps.repairs.tasks.fill_analytics.inputs import RepairInputs, inputs_fingerprint

# Даты ABAI и замеров хранятся наивными — как в БД.
_NOW = datetime(2026, 9, 9, 12, 0)  # noqa: DTZ001
_START = datetime(2026, 9, 1)  # noqa: DTZ001
_END = datetime(2026, 9, 5)  # noqa: DTZ001


def _repair(
    *,
    start: datetime = _START,
    end: datetime | None = _END,
) -> SimpleNamespace:
    return SimpleNamespace(id=1, start_time=start, end_time=end)


# --- окна и закрытость замера -----------------------------------------------


def test_repair_window_extends_a_day_past_end() -> None:
    assert repair_window(_repair(), now=_NOW) == (_START, _END + timedelta(days=1))


def test_repair_window_of_active_repair_ends_now_plus_day() -> None:
    assert repair_window(_repair(end=None), now=_NOW) == (
        _START,
        _NOW + timedelta(days=1),
    )


def test_measure_closed_after_poller_grace() -> None:
    grace = timedelta(minutes=120)
    assert is_measure_closed(_NOW - timedelta(minutes=121), now=_NOW, grace=grace)
    assert not is_measure_closed(_NOW - timedelta(minutes=119), now=_NOW, grace=grace)
    assert not is_measure_closed(None, now=_NOW, grace=grace)


def test_measure_overlaps_uses_start_when_end_unknown() -> None:
    window = (_START, _END)
    assert measure_overlaps(
        measure_start=_END - timedelta(hours=1),
        measure_end=None,
        window=window,
    )
    assert not measure_overlaps(
        measure_start=_END + timedelta(hours=1),
        measure_end=None,
        window=window,
    )
    assert not measure_overlaps(measure_start=None, measure_end=_END, window=window)


def test_measure_overlaps_when_measure_started_before_repair() -> None:
    # Замер начался до ремонта, но продолжался внутри окна — сопоставим.
    assert measure_overlaps(
        measure_start=_START - timedelta(days=1),
        measure_end=_START + timedelta(hours=3),
        window=(_START, _END),
    )


# --- Toucan: владельцы и номера скважин --------------------------------------


def test_owner_and_ngdu_mapping_are_inverse() -> None:
    for owner_id in (2, 3, 4, 5):
        ngdu = ngdu_abai_id_by_owner(owner_id)
        assert ngdu is not None
        assert owner_id_by_ngdu_abai_id(ngdu) == owner_id
    assert ngdu_abai_id_by_owner(999) is None


def test_extract_well_number_strips_prefix_and_zeros() -> None:
    assert extract_well_number("BLG_0177") == 177
    assert extract_well_number("VMB_12A") == 12
    assert extract_well_number("no-digits") is None


# --- документы ABAI: решение по хэшу -----------------------------------------


def _doc(
    *,
    por: int | None = 10,
    act: int | None = None,
    digest: str | None = "h1",
) -> SimpleNamespace:
    return SimpleNamespace(por_file_id=por, act_file_id=act, source_hash=digest)


def test_plan_doc_update_uploads_when_missing_or_changed() -> None:
    assert plan_doc_update(None, digest="h1", is_finished=False) == "upload"
    assert plan_doc_update(_doc(por=None), digest="h1", is_finished=False) == "upload"
    assert (
        plan_doc_update(_doc(digest="old"), digest="h1", is_finished=True) == "upload"
    )


def test_plan_doc_update_marks_act_without_reupload() -> None:
    # PDF тот же, ремонт завершился, акт ещё не отмечен — только ссылка.
    assert plan_doc_update(_doc(), digest="h1", is_finished=True) == "set_act"
    assert plan_doc_update(_doc(act=10), digest="h1", is_finished=True) == "skip"
    assert plan_doc_update(_doc(), digest="h1", is_finished=False) == "skip"


def test_content_digest_is_stable_sha256() -> None:
    assert content_digest(b"abc") == content_digest(b"abc")
    assert len(content_digest(b"abc")) == 64
    assert content_digest(b"abc") != content_digest(b"abd")


# --- СПО: актуальность сохранённого замера -----------------------------------


def _spo(*, raw_size: int | None = 100, files: bool = True) -> SimpleNamespace:
    file_id = 1 if files else None
    return SimpleNamespace(
        id=7,
        raw_size=raw_size,
        chart_file_id=file_id,
        chart_json_file_id=file_id,
        notes_file_id=file_id,
        passport_file_id=file_id,
    )


def test_is_up_to_date_requires_same_size_and_all_files() -> None:
    assert is_up_to_date(_spo(), raw_size=100)
    assert not is_up_to_date(_spo(), raw_size=150)
    assert not is_up_to_date(_spo(raw_size=None), raw_size=100)
    assert not is_up_to_date(_spo(files=False), raw_size=100)
    assert not is_up_to_date(None, raw_size=100)


# --- отпечаток входов общего вердикта ----------------------------------------


def test_inputs_fingerprint_ignores_spo_order_but_not_size() -> None:
    base = {"before_id": 1, "after_id": 2, "por_file_id": 3, "act_file_id": None}
    a = inputs_fingerprint(spo_revisions={5: 100, 6: 200}, **base)
    b = inputs_fingerprint(spo_revisions={6: 200, 5: 100}, **base)
    grown = inputs_fingerprint(spo_revisions={5: 150, 6: 200}, **base)
    assert a == b
    assert a != grown
    assert a != inputs_fingerprint(
        spo_revisions={5: 100, 6: 200},
        **{**base, "act_file_id": 3},
    )


def test_repair_inputs_only_closed_spos_feed_fingerprint_and_ai() -> None:
    live = SimpleNamespace(id=1, raw_size=10, kbrs_measure_id=100)
    closed = SimpleNamespace(id=2, raw_size=20, kbrs_measure_id=101)
    inputs = RepairInputs(
        before=None,
        after=None,
        doc=None,
        spos=[live, closed],
        closed_spo_ids=frozenset({2}),
    )
    assert inputs.primary_spo is live
    assert inputs.primary_spo_closed is False
    assert inputs.closed_spos == [closed]
    assert inputs.fingerprint() == inputs_fingerprint(
        before_id=None,
        after_id=None,
        spo_revisions={2: 20},
        por_file_id=None,
        act_file_id=None,
    )


# --- УТО: пары «машина × дата» и debounce ------------------------------------


def test_unique_car_date_pairs_dedupes_and_skips_blank_cars() -> None:
    day = _START.date()
    summaries = [
        SimpleNamespace(car=" A123 ", date=day),
        SimpleNamespace(car="A123", date=day),
        SimpleNamespace(car="", date=day),
        SimpleNamespace(car="B777", date=None),
        SimpleNamespace(car="B777", date=day),
    ]
    assert UtoTransportFetcher._unique_car_date_pairs(summaries) == [  # noqa: SLF001
        ("A123", day),
        ("B777", day),
    ]


def test_analytics_debounce_key_is_per_repair() -> None:
    assert analytics_debounce_key(5) != analytics_debounce_key(6)
    assert analytics_debounce_key(5).endswith(":5")
