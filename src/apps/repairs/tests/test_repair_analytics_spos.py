"""СПО на странице аналитики ремонта: все замеры окна, а не первый."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

from apps.repairs.use_cases.get_repair_analytics_view import (
    GetRepairAnalyticsViewUseCase,
)

START = datetime(2026, 10, 28, 9, 0)  # noqa: DTZ001
END = datetime(2026, 11, 3, 18, 0)  # noqa: DTZ001
NOW = datetime.now(tz=UTC)
AI_RESULT = {
    "status": "completed",
    "model_name": "m",
    "prompt_version": "v1",
    "result": {"raw": "разбор"},
    "error": None,
    "processed_at": NOW,
}


def _spo(spo_id: int, day: int, *, notes: int | None, passport: int | None) -> object:
    return SimpleNamespace(
        id=spo_id,
        file_id=spo_id * 10,
        chart_file_id=None,
        chart_json_file_id=None,
        notes_file_id=notes,
        passport_file_id=passport,
        snapshot_time=datetime(2026, 10, day, 17, 32),  # noqa: DTZ001
        well_id=7,
        kbrs_measure_id=None,
        raw_size=None,
    )


class _SPOs:
    def __init__(self, spos: list) -> None:
        self.spos = spos
        self.calls: list[tuple] = []

    async def list_by_well_id_in_window(
        self,
        well_id: int,
        start: datetime,
        end: datetime | None,
    ) -> list:
        self.calls.append((well_id, start, end))
        return self.spos


class _AI:
    def __init__(self, spo_ids: list[int]) -> None:
        self.spo_ids = spo_ids

    async def list_by_spo_ids(self, spo_ids: list[int]) -> list:
        return [
            SimpleNamespace(id=i, spo_id=i, **AI_RESULT)
            for i in spo_ids
            if i in self.spo_ids
        ]


class _Files:
    async def list_by_ids(self, file_ids: list[int]) -> list:
        return [SimpleNamespace(id=i, file=f"key/{i}") for i in file_ids]


class _Storage:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads

    async def download_file(self, path: str) -> BytesIO:
        payload = self.payloads[path]
        if isinstance(payload, Exception):
            raise payload
        return BytesIO(json.dumps(payload).encode("utf-8"))


def _use_case(spos: _SPOs, ai: _AI, storage: _Storage) -> GetRepairAnalyticsViewUseCase:
    deps = dict.fromkeys(
        (
            "repair_repository",
            "wells_repository",
            "repair_brigade_repository",
            "unique_brigade_repository",
            "analytics_repository",
            "analytics_dynamogram_repository",
            "dynamogram_repository",
            "dynamogram_ai_repository",
            "overall_ai_repository",
            "transport_repository",
            "cm_brigade_repository",
            "cm_brigade_error_screen_repository",
        ),
    )
    return GetRepairAnalyticsViewUseCase(
        **deps,
        spo_repository=spos,
        spo_ai_repository=ai,
        file_repository=_Files(),
        storage=storage,
        cm_media_url_header="http://cm",
    )


def test_page_shows_every_spo_of_repair_window_in_order() -> None:
    spos = _SPOs(
        [
            _spo(1, 28, notes=101, passport=201),
            _spo(2, 29, notes=102, passport=None),
            _spo(3, 30, notes=None, passport=None),
        ],
    )
    storage = _Storage(
        {
            "key/101": {"start": "2026-10-28T17:32:59", "row_count": 143},
            "key/102": {"start": "2026-10-29T08:02:59", "row_count": 2077},
            "key/201": {"brigade": 13, "workshop": 3, "well": 1},
        },
    )
    use_case = _use_case(spos, _AI([1, 3]), storage)
    repair = SimpleNamespace(id=1, start_time=START, end_time=END)

    result = asyncio.run(use_case._build_spos(repair, 7))  # noqa: SLF001

    assert [dto.id for dto in result] == [1, 2, 3]
    assert spos.calls == [(7, START, END + timedelta(days=1))]
    assert [dto.row_count for dto in result] == [143, 2077, None]
    assert result[0].start_time == datetime(2026, 10, 28, 17, 32, 59)  # noqa: DTZ001
    assert result[0].passport is not None
    assert result[0].passport.brigade == 13
    assert result[1].passport is None
    assert [dto.ai_result is not None for dto in result] == [True, False, True]


def test_unreadable_side_file_does_not_break_page() -> None:
    spos = _SPOs([_spo(1, 28, notes=101, passport=201)])
    storage = _Storage(
        {"key/101": RuntimeError("s3 down"), "key/201": ["not", "a", "dict"]},
    )
    use_case = _use_case(spos, _AI([]), storage)
    repair = SimpleNamespace(id=1, start_time=START, end_time=END)

    (dto,) = asyncio.run(use_case._build_spos(repair, 7))  # noqa: SLF001

    assert dto.start_time is None
    assert dto.row_count is None
    assert dto.passport is None


def test_no_spo_in_window_gives_empty_list() -> None:
    use_case = _use_case(_SPOs([]), _AI([]), _Storage({}))
    repair = SimpleNamespace(id=1, start_time=START, end_time=None)

    assert asyncio.run(use_case._build_spos(repair, 7)) == []  # noqa: SLF001
