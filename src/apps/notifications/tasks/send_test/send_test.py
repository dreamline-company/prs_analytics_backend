"""Тестовое уведомление ``system.test``: журнал → Redis → хабы → сокеты.

Запуск в контейнере API (из ``src/``)::

    python -m apps.notifications.tasks.send_test --severity alarm
    python -m apps.notifications.tasks.send_test --severity warning --well VMB_2051
    python -m apps.notifications.tasks.send_test --severity info --source-id 1

``--well`` привязывает уведомление к скважине (проверка перехода стены),
одинаковый ``--source-id`` у нескольких вызовов даёт цепочку в карточке.
Пишет только при ``NOTIFICATIONS_ENABLED=true``: тот же путь, что у
производителей. HTTP-эндпоинта для теста нет — на открытом API это дыра.
"""

import argparse
import sys
from dataclasses import dataclass
from uuid import uuid4

from apps.notifications.constants import SEVERITIES, local_now
from apps.notifications.dto.internal.notification import (
    NotificationCreateDTO,
    NotificationFactDTO,
)
from apps.notifications.services.emitter import NotificationEmitter
from apps.notifications.services.publisher import publish_notifications
from apps.wells.repositories.well import WellRepository

EVENT_CODE = "system.test"
SOURCE_TYPE = "system_test"
SEVERITY_TITLES_RU = {
    "info": "информация",
    "warning": "предупреждение",
    "alarm": "авария",
}


class SendTestError(Exception):
    pass


@dataclass(frozen=True)
class SendTestArgs:
    severity: str
    well: str | None = None
    source_id: int | None = None


def build_notification(
    args: SendTestArgs,
    *,
    well_id: int | None,
) -> NotificationCreateDTO:
    now = local_now()
    level = SEVERITY_TITLES_RU[args.severity]
    subject = f"{args.well}: " if args.well else ""
    facts = [
        NotificationFactDTO(label_ru="Уровень", value_ru=level),
        NotificationFactDTO(label_ru="Время", value_ru=now.strftime("%d.%m %H:%M:%S")),
        NotificationFactDTO(label_ru="Источник", value_ru="send_test (CLI)"),
    ]
    return NotificationCreateDTO(
        event_code=EVENT_CODE,
        severity=args.severity,  # type: ignore[arg-type]
        title_ru=f"{subject}проверка уведомлений ({level})",
        body_ru=(
            "Тестовое уведомление: путь журнал → Redis → WebSocket. "
            "Действий не требуется."
        ),
        well_id=well_id,
        source_type=SOURCE_TYPE if args.source_id is not None else None,
        source_id=args.source_id,
        dedup_key=f"{EVENT_CODE}:{now:%Y-%m-%dT%H:%M:%S}:{uuid4().hex[:8]}",
        payload={"sent_by": "send_test"},
        facts=facts,
        event_at=now,
    )


async def send_test(args: SendTestArgs) -> int:
    """id записанного уведомления."""
    from shared.database.sql.setup import session_makers  # noqa: PLC0415

    emitter = NotificationEmitter()
    if not emitter.enabled:
        msg = "NOTIFICATIONS_ENABLED=false: уведомление не записано"
        raise SendTestError(msg)

    async with session_makers["app"]() as session:
        well_id = None
        if args.well:
            well = await WellRepository(session).get_by_name(args.well)
            if well is None:
                msg = f"Скважина {args.well} не найдена"
                raise SendTestError(msg)
            well_id = well.id
        notification_id = await emitter.emit(
            session,
            build_notification(args, well_id=well_id),
        )
        await session.commit()
    if notification_id is None:
        msg = "Уведомление не записано (дубль dedup_key?)"
        raise SendTestError(msg)
    await publish_notifications([notification_id])
    return notification_id


def parse_args(argv: list[str] | None = None) -> SendTestArgs:
    parser = argparse.ArgumentParser(
        prog="python -m apps.notifications.tasks.send_test",
        description="Записать и опубликовать тестовое уведомление system.test",
    )
    parser.add_argument("--severity", choices=SEVERITIES, default="info")
    parser.add_argument("--well", help="имя скважины, например VMB_2051")
    parser.add_argument(
        "--source-id",
        type=int,
        help="общий источник для цепочки (source_type=system_test)",
    )
    ns = parser.parse_args(argv)
    return SendTestArgs(severity=ns.severity, well=ns.well, source_id=ns.source_id)


def main(argv: list[str] | None = None) -> int:
    from apps.celery_app import run_async  # noqa: PLC0415

    args = parse_args(argv)
    try:
        notification_id = run_async(send_test(args))
    except SendTestError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    sys.stdout.write(f"notification id={notification_id} ({args.severity})\n")
    return 0
