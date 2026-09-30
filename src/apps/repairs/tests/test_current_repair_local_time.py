"""Идущий ремонт ищется по местному времени — как время ремонтов ABAI."""

import asyncio
from datetime import UTC, datetime, tzinfo

import pytest

from apps.repairs.services import current_repair

RUN_AT_UTC = datetime(2026, 9, 30, 9, 30, tzinfo=UTC)  # 14:30 по Атырау


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return RUN_AT_UTC.astimezone(tz) if tz else RUN_AT_UTC.replace(tzinfo=None)


class _Repairs:
    def __init__(self) -> None:
        self.asked_now: datetime | None = None

    async def list_current_by_abai_well_ids(self, _ids: list, *, now: datetime) -> dict:
        self.asked_now = now
        return {}


def test_current_repair_uses_local_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(current_repair, "datetime", _FixedDatetime)
    repairs = _Repairs()

    asyncio.run(
        current_repair.CurrentRepairService(repairs).get_for_wells([1]),  # type: ignore[arg-type]
    )

    assert repairs.asked_now == datetime(2026, 9, 30, 14, 30)  # noqa: DTZ001
