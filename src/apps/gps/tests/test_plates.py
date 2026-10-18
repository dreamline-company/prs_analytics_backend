"""Сверка госномеров Аргуса с путёвками УТО и сводками ПРС.

Кейсы — из сверки 14.10.2026 (docs/argus_api.md).
"""

from apps.gps.services.plates import (
    canonical_plate,
    normalize_plate,
    plate_candidates,
    split_vehicles,
    wanted_plates,
)


def test_normalize_plate_cyrillic_spaces_and_suffix() -> None:
    assert normalize_plate("730AU06") == "730AU06"
    assert normalize_plate(" 720 АU 06 ") == "720AU06"  # кириллическая «А»
    assert normalize_plate("010IZO06 ВО") == "010IZO06"  # суффикс Аргуса
    assert normalize_plate("564-ay-06") == "564AY06"
    assert normalize_plate("") is None
    assert normalize_plate(None) is None


def test_e_plates_share_one_canonical_form() -> None:
    # Аргус пишет и E232ALD, и ALD220E; в сводке — E220ALD06.
    assert canonical_plate("E232ALD") == "E232ALD"
    assert canonical_plate("ALD220E") == "E220ALD"
    assert canonical_plate("E220ALD06") == "E220ALD"
    assert canonical_plate("AZD480E") == "E480AZD"


def test_plate_candidates_add_region_and_e_variant() -> None:
    assert "720AU06" in plate_candidates("720AU")
    assert plate_candidates("237ALD") == {"237ALD", "237ALD06", "E237ALD"}
    assert plate_candidates("E220ALD06") == {"E220ALD"}
    assert plate_candidates("730AU06") == {"730AU06"}
    assert plate_candidates(None) == set()


def test_summary_plates_match_argus_names() -> None:
    argus = {canonical_plate(name) for name in ("720AU06", "ALD220E", "E237ALD")}
    for summary_car in ("720AU", "E220ALD06", "237ALD"):
        assert plate_candidates(summary_car) & argus, summary_car


def test_split_vehicles_and_wanted_plates() -> None:
    assert split_vehicles("720AU06, 564AY06") == ["720AU06", "564AY06"]
    assert split_vehicles("720AU06 и 564AY06") == ["720AU06", "564AY06"]
    assert split_vehicles(None) == []
    assert wanted_plates(["720AU", None, "564AY06/193AS06"]) >= {
        "720AU06",
        "564AY06",
        "193AS06",
    }
