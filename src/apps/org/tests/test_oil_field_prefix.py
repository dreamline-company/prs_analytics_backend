"""Месторождение как префикс имени скважины."""

from types import SimpleNamespace

from apps.org.services import well_name_prefix, wells_with_prefix


def test_well_name_prefix() -> None:
    assert well_name_prefix("BLG_0177") == "BLG"
    assert well_name_prefix("VMB_0001") == "VMB"
    assert well_name_prefix("00001-CT") is None
    assert well_name_prefix("BLG0177") is None
    assert well_name_prefix("") is None


def test_wells_with_prefix_keeps_only_matching_wells() -> None:
    wells = [
        SimpleNamespace(name="BJR_0001"),
        SimpleNamespace(name="BJR_0002"),
        SimpleNamespace(name="BJRX_0003"),  # другой префикс, хоть и начинается так же
        SimpleNamespace(name="KNM_0004"),
        SimpleNamespace(name="00001-CT"),
    ]

    assert [w.name for w in wells_with_prefix(wells, "BJR")] == ["BJR_0001", "BJR_0002"]
    assert wells_with_prefix(wells, "ZZZ") == []
