"""R10 не считает сутки, пока загрузка замеров ЦИТС стоит."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from apps.detectors.cits_events.incident_config import FEED_MAX_GAP_HOURS
from apps.detectors.cits_events.tasks.run_incidents.run_incidents import (
    CitsEventsRunner,
)

AT = datetime(2026, 11, 5, 0, 0)  # noqa: DTZ001


def _runner(last: datetime | None) -> CitsEventsRunner:
    runner = CitsEventsRunner(MagicMock(), now=AT)
    runner.telemetry_repo = MagicMock(
        get_last_by_ngdu_id=AsyncMock(
            return_value=SimpleNamespace(date_time=last) if last else None,
        ),
    )
    return runner


def test_feed_freshness() -> None:
    fresh = AT - timedelta(hours=1)
    stale = AT - timedelta(hours=FEED_MAX_GAP_HOURS + 1)

    assert asyncio.run(_runner(fresh).feed_fresh(11, AT))
    assert not asyncio.run(_runner(stale).feed_fresh(11, AT))
    assert not asyncio.run(_runner(None).feed_fresh(11, AT))
