"""Газы в разборе СПО: модель выдавала вес на крюке за H2S, когда газоанализатора
в замере нет («выброс 10.331 мг/м3» у UAZ_0043 — это максимум веса, т)."""

from datetime import datetime
from types import SimpleNamespace

from apps.repairs.tasks.fill_analytics.ai.spo_processor import SPOAIProcessor

HEADER = "timestamp,datetime,hook_weight_t,h2s_mg_m3,ch4_percent"


def _prompt(chart: str) -> str:
    item = SimpleNamespace(
        spo=SimpleNamespace(id=1934, snapshot_time=datetime(2026, 10, 1, 8, 0)),  # noqa: DTZ001
        chart_text=chart,
        notes_text=None,
    )
    processor = SPOAIProcessor(agent=None)  # type: ignore[arg-type]
    state = processor._build_state(item)  # type: ignore[arg-type]  # noqa: SLF001
    return state["messages"][0].content


def test_no_gas_sensor_no_gas_columns() -> None:
    prompt = _prompt(
        f"{HEADER}\n1,2026-10-01T18:38:50,0.002,,\n2,2026-10-01T18:38:51,10.331,,\n",
    )

    assert "H2S: no sensor data in this measurement" in prompt
    assert "CH4: no sensor data in this measurement" in prompt
    assert "h2s_mg_m3" not in prompt
    assert "2,2026-10-01T18:38:51,10.331\n" in prompt


def test_gas_peak_is_computed_by_code() -> None:
    prompt = _prompt(
        f"{HEADER}\n1,2026-09-30T10:00:00,5.0,0.4,\n2,2026-09-30T10:00:01,5.1,5.9,\n",
    )

    assert "H2S max 5.9 mg/m3 at 2026-09-30T10:00:01" in prompt
    assert "CH4: no sensor data in this measurement" in prompt
    assert "h2s_mg_m3" in prompt
    assert "ch4_percent" not in prompt
