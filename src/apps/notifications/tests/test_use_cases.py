"""История, отметка «просмотрено», сборка элемента, публикация, send_test."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.notifications.dto.internal.notification import (
    NotificationRowDTO,
    NotificationViewRowDTO,
)
from apps.notifications.dto.queries.notification import (
    ListNotificationsQuery,
    NotificationFilterDTO,
)
from apps.notifications.services import publisher
from apps.notifications.services.items import NotificationItemBuilder
from apps.notifications.tasks.send_test import send_test as send_test_module
from apps.notifications.use_cases.list_notifications import ListNotificationsUseCase
from apps.notifications.use_cases.mark_viewed import MarkNotificationsViewedUseCase

NOW = datetime(2026, 11, 22, 12, 0, 0)  # noqa: DTZ001 — местное наивное


def _row(notification_id: int, **overrides: Any) -> NotificationRowDTO:  # noqa: ANN401
    data: dict[str, Any] = {
        "id": notification_id,
        "event_code": "r2.escalated",
        "severity": "alarm",
        "title_ru": f"#{notification_id}",
        "body_ru": None,
        "well_id": None,
        "abai_ngdu_id": None,
        "source_type": None,
        "source_id": None,
        "payload": {},
        "event_at": NOW,
        "created_at": NOW,
        "viewed_at": None,
    }
    data.update(overrides)
    return NotificationRowDTO(**data)


class _Wells:
    async def list_by_ids(self, ids: list[int]) -> list[Any]:
        wells = {
            812: SimpleNamespace(id=812, name="VMB_2051", abai_id=9001),
            900: SimpleNamespace(id=900, name="ZHM_0001", abai_id=9002),
            901: SimpleNamespace(id=901, name="ZHM_0002", abai_id=9003),
        }
        return [wells[i] for i in ids if i in wells]


class _Orgs:
    async def list_by_abai_ids(self, ids: list[int]) -> list[Any]:
        orgs = {
            12: SimpleNamespace(id=5, abai_id=12, name_ru="Кайнармунайгаз"),
            11: SimpleNamespace(id=4, abai_id=11, name_ru="Жайкмунайгаз"),
        }
        return [orgs[i] for i in ids if i in orgs]

    async def get_by_id(self, org_id: int) -> Any:  # noqa: ANN401
        return {5: SimpleNamespace(id=5, abai_id=12)}.get(org_id)


class _Expl:
    async def get_latest_expl_name_by_abai_well_ids(
        self,
        ids: list[int],  # noqa: ARG002
    ) -> dict[int, str | None]:
        return {9001: "ЭВН", 9002: " шгн ", 9003: "Отсутствует"}


def _builder() -> NotificationItemBuilder:
    return NotificationItemBuilder(
        well_repository=_Wells(),  # type: ignore[arg-type]
        org_repository=_Orgs(),  # type: ignore[arg-type]
        well_expl_repository=_Expl(),  # type: ignore[arg-type]
    )


def test_item_well_block_uses_org_id_and_matrix_exploitation() -> None:
    rows = [
        _row(1, well_id=812, abai_ngdu_id=12),
        _row(2, well_id=900, abai_ngdu_id=11),
        _row(3, well_id=901, abai_ngdu_id=None),
        _row(4),
    ]
    items = asyncio.run(_builder().build(rows))
    assert items[0].well is not None
    assert items[0].well.model_dump() == {
        "id": 812,
        "name": "VMB_2051",
        "ngdu_id": 5,
        "ngdu_name": "Кайнармунайгаз",
        "exploitation": "evn",
    }
    assert items[1].well is not None
    assert (items[1].well.ngdu_id, items[1].well.exploitation) == (4, "shgn")
    assert items[2].well is not None
    assert (items[2].well.ngdu_id, items[2].well.exploitation) == (None, None)
    assert items[3].well is None


def test_card_facts_and_chain() -> None:
    row = _row(
        2,
        source_type="detector_incident",
        source_id=7,
        payload={
            "facts": [
                {"label_ru": "Момент", "value_ru": "42 %"},
                {"broken": True},
            ],
        },
    )
    chain = [_row(1, severity="warning"), row]
    card = asyncio.run(_builder().card(row, chain))
    assert [f.model_dump() for f in card.facts] == [
        {"label_ru": "Момент", "value_ru": "42 %"},
    ]
    assert [c.id for c in card.chain] == [1, 2]


class _History:
    def __init__(self, total: int) -> None:
        self.total = total
        self.filters: list[NotificationFilterDTO] = []

    async def list_rows(self, flt: NotificationFilterDTO) -> list[NotificationRowDTO]:
        self.filters.append(flt)
        ids = [
            i
            for i in range(self.total, 0, -1)
            if flt.before_id is None or i < flt.before_id
        ]
        return [_row(i) for i in ids[: flt.limit]]


def _history(repo: _History, **query: Any) -> Any:  # noqa: ANN401
    use_case = ListNotificationsUseCase(
        notification_repository=repo,  # type: ignore[arg-type]
        org_repository=_Orgs(),  # type: ignore[arg-type]
        item_builder=_builder(),
    )
    return asyncio.run(use_case.execute(ListNotificationsQuery(**query)))


def test_history_pages_with_next_before_id() -> None:
    repo = _History(total=5)
    first = _history(repo, limit=2)
    assert [i.id for i in first.items] == [5, 4]
    assert first.next_before_id == 4
    last = _history(repo, limit=2, before_id=2)
    assert [i.id for i in last.items] == [1]
    assert last.next_before_id is None
    exact = _history(repo, limit=5)
    assert exact.next_before_id is None


def test_history_ngdu_filter_is_org_id() -> None:
    repo = _History(total=1)
    _history(repo, ngdu_id=5, severities=["alarm"], viewed=False)
    assert repo.filters[-1].abai_ngdu_id == 12
    assert repo.filters[-1].severities == ["alarm"]
    assert repo.filters[-1].viewed is False
    unknown = _history(repo, ngdu_id=999)
    assert unknown.items == []


def test_history_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severities"):
        ListNotificationsQuery(severities=["fatal"])  # type: ignore[list-item]


class _Session:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def commit(self) -> None:
        self.log.append("commit")


class _Notifications:
    async def is_present(self, notification_id: int) -> bool:
        return notification_id != 404

    async def count_unviewed_by_severity(self) -> dict[str, int]:
        return {"warning": 1}


class _Views:
    def __init__(self) -> None:
        self.up_to: list[int] = []

    async def mark_one(self, nid: int, at: datetime) -> list[NotificationViewRowDTO]:
        return [NotificationViewRowDTO(view_id=50, notification_id=nid, viewed_at=at)]

    async def mark_up_to(
        self,
        up_to_id: int,
        at: datetime,
    ) -> list[NotificationViewRowDTO]:
        self.up_to.append(up_to_id)
        return [
            NotificationViewRowDTO(view_id=60 + i, notification_id=i, viewed_at=at)
            for i in range(1, up_to_id + 1)
        ]


def test_view_all_marks_up_to_id_commits_then_publishes() -> None:
    log: list[str] = []
    views = _Views()

    async def publish(ids: Any) -> None:  # noqa: ANN401
        log.append(f"publish:{list(ids)}")

    use_case = MarkNotificationsViewedUseCase(
        session=_Session(log),  # type: ignore[arg-type]
        notification_repository=_Notifications(),  # type: ignore[arg-type]
        view_repository=views,  # type: ignore[arg-type]
        publish=publish,
        clock=lambda: NOW,
    )
    result = asyncio.run(use_case.mark_up_to(2))
    assert views.up_to == [2]
    assert result.ids == [1, 2]
    assert result.viewed_at == NOW
    assert result.unviewed.model_dump() == {"alarm": 0, "warning": 1, "info": 0}
    assert log == ["commit", "publish:[61, 62]"]


def test_publish_with_redis_down_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        publisher,
        "get_settings",
        lambda: SimpleNamespace(APP_REDIS_DB_URL="redis://127.0.0.1:1/0"),
    )
    asyncio.run(publisher.publish_notifications([1, None, 2]))
    asyncio.run(publisher.publish_views([None]))


def test_send_test_refuses_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.notifications.services.emitter.get_settings",
        lambda: SimpleNamespace(NOTIFICATIONS_ENABLED=False),
    )
    assert send_test_module.main(["--severity", "alarm"]) == 1


def test_send_test_notification_shape() -> None:
    args = send_test_module.SendTestArgs(
        severity="warning",
        well="VMB_2051",
        source_id=3,
    )
    dto = send_test_module.build_notification(args, well_id=812)
    assert dto.event_code == "system.test"
    assert dto.severity == "warning"
    assert dto.title_ru.startswith("VMB_2051: ")
    assert (dto.source_type, dto.source_id, dto.well_id) == ("system_test", 3, 812)
    assert dto.payload_with_facts()["facts"]
    other = send_test_module.build_notification(args, well_id=812)
    assert other.dedup_key != dto.dedup_key
