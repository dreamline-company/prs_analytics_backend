"""Экраны ошибок бригады за время ремонта и 404 страницы аналитики ремонта."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.repairs.dto.queries.analytics_view import GetRepairAnalyticsViewQuery
from apps.repairs.services.error_screens import list_repair_error_screens
from apps.repairs.use_cases.get_repair_analytics_view import (
    GetRepairAnalyticsViewUseCase,
    RepairAnalyticsNotFoundError,
)

START = datetime(2026, 9, 20, 8, 0)  # noqa: DTZ001
END = datetime(2026, 9, 22, 18, 0)  # noqa: DTZ001


class _Links:
    def __init__(self, brigade_id: int | None) -> None:
        self.brigade_id = brigade_id

    async def get_by_repair_id(self, _repair_id: int) -> SimpleNamespace | None:
        if self.brigade_id is None:
            return None
        return SimpleNamespace(brigade_id=self.brigade_id)


class _Brigades:
    def __init__(self, name: str) -> None:
        self.name = name

    async def get_by_id(self, _brigade_id: int) -> SimpleNamespace:
        return SimpleNamespace(name=self.name)


class _CMBrigades:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def list_by_name(self, number: str) -> list[SimpleNamespace]:
        self.asked.append(number)
        return [SimpleNamespace(id=7), SimpleNamespace(id=9)]


class _Screens:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_by_brigade_ids_in_range(
        self,
        brigade_ids: list[int],
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[str]:
        self.calls.append((brigade_ids, start_time, end_time))
        return ["screen"]


def _run(
    *,
    brigade_id: int | None = 1,
    name: str = "Бригада ПРС №12",
    end: datetime | None = END,
) -> tuple[list, _CMBrigades, _Screens]:
    cm, screens = _CMBrigades(), _Screens()
    result = asyncio.run(
        list_repair_error_screens(
            SimpleNamespace(id=100, start_time=START, end_time=end),
            repair_brigade_repo=_Links(brigade_id),
            unique_brigade_repo=_Brigades(name),
            cm_brigade_repo=cm,
            cm_brigade_error_screen_repo=screens,
        ),
    )
    return list(result), cm, screens


def test_screens_of_brigade_number_within_repair() -> None:
    result, cm, screens = _run()

    assert result == ["screen"]
    assert cm.asked == ["12"]
    assert screens.calls == [([7, 9], START, END)]


def test_open_repair_is_searched_until_now() -> None:
    _, _, screens = _run(end=None)

    assert screens.calls[0][2] > END


def test_no_screens_without_brigade_or_its_number() -> None:
    assert _run(brigade_id=None)[0] == []
    assert _run(name="Бригада без номера")[0] == []


def test_unknown_repair_is_404_before_well_lookup() -> None:
    class _Repairs:
        async def get_by_id(self, _repair_id: int) -> None:
            return None

    class _Wells:
        async def get_by_abai_id(self, **_kwargs: int) -> None:
            pytest.fail("well must not be looked up for an unknown repair")

    deps = dict.fromkeys(
        (
            "repair_brigade_repository",
            "unique_brigade_repository",
            "analytics_repository",
            "analytics_dynamogram_repository",
            "analytics_spo_repository",
            "dynamogram_repository",
            "spo_repository",
            "dynamogram_ai_repository",
            "spo_ai_repository",
            "overall_ai_repository",
            "transport_repository",
            "file_repository",
            "storage",
            "cm_brigade_repository",
            "cm_brigade_error_screen_repository",
        ),
    )
    use_case = GetRepairAnalyticsViewUseCase(
        repair_repository=_Repairs(),
        wells_repository=_Wells(),
        cm_media_url_header="http://cm",
        **deps,
    )

    with pytest.raises(RepairAnalyticsNotFoundError):
        asyncio.run(use_case.execute(GetRepairAnalyticsViewQuery(repair_id=1)))
