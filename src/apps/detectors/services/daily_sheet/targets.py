"""Подключённые НГДУ как простые значения — для утренних тасков ведомостей.

ORM-объект ``Org`` после ``session.rollback()`` протухает, и обращение к
``org.id`` в следующей итерации лезет в БД вне greenlet'а (MissingGreenlet).
Таски ведомостей откатывают сессию на каждом «нет телеметрии», поэтому НГДУ
снимаются в неизменяемые записи один раз до цикла.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.repositories.org import OrgRepository
from apps.org.use_cases.list_ngdus import LISTED_NGDU_ABAI_IDS


@dataclass(frozen=True, slots=True)
class NgduRef:
    id: int
    abai_id: int
    name_ru: str


async def list_target_ngdus(
    session: AsyncSession,
    *,
    ngdu_id: int | None = None,
) -> list[NgduRef]:
    """Один НГДУ по ``org.id`` или все подключённые (как в /org/v1/ngdus)."""
    repo = OrgRepository(session)
    if ngdu_id is not None:
        org = await repo.get_by_id(ngdu_id)
        orgs = [org] if org is not None else []
    else:
        orgs = list(
            await repo.list_by_abai_ids([item.value for item in LISTED_NGDU_ABAI_IDS]),
        )
    return [
        NgduRef(id=org.id, abai_id=org.abai_id, name_ru=org.name_ru) for org in orgs
    ]
