"""Бригада нашей оргструктуры -> бригады CM (камеры, экраны нарушений ТБ).

В CM бригада — номер (``main_brigade.name``) внутри НГДУ
(``main_brigade.ngdu_id``): номера 1–8 есть во всех четырёх НГДУ, и по одному
номеру к бригаде попадали экраны всех одноимённых. ``ngdu_id`` в CM — свой
справочник (``main_ngdu``), сверен с НГДУ ABAI по названиям 30.09.2026.
"""

import re
from collections import defaultdict
from collections.abc import Iterable

from apps.org.models.brigade import UniqueBrigade
from apps.org.repositories.brigade import UniqueBrigadeRepository
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

CM_NGDU_ID_BY_ABAI: dict[int, int] = {
    AbaiNGDUIDsEnum.KMG: 1,
    AbaiNGDUIDsEnum.ZHlMG: 2,
    AbaiNGDUIDsEnum.DMG: 3,
    AbaiNGDUIDsEnum.ZHMG: 4,
}

_NUMBER_RE = re.compile(r"№\s*(\d+)")


def brigade_number(name: str) -> str | None:
    """Номер из «Бригада №13»; у Кезби-3, ММС-1 и подобных — ``None``."""
    match = _NUMBER_RE.search(name)
    return match.group(1) if match else None


async def match_cm_brigades(
    brigades: Iterable[UniqueBrigade],
    *,
    unique_brigade_repo: UniqueBrigadeRepository,
    cm_brigade_repo: CMBrigadeRepository,
) -> dict[int, list[int]]:
    """id нашей бригады -> id бригад CM с тем же номером в том же НГДУ.

    Бригада без номера, из НГДУ без пары в CM или без такой бригады в CM в
    результат не попадает.
    """
    numbers = {
        brigade.id: number
        for brigade in brigades
        if (number := brigade_number(brigade.name)) is not None
    }
    if not numbers:
        return {}
    ngdu_abai_ids = await unique_brigade_repo.map_ngdu_abai_ids(list(numbers))
    cm_ids: dict[tuple[str, int], list[int]] = defaultdict(list)
    for cm in await cm_brigade_repo.list_by_names(sorted(set(numbers.values()))):
        cm_ids[(cm.name, cm.ngdu_id)].append(cm.id)

    result: dict[int, list[int]] = {}
    for brigade_id, number in numbers.items():
        cm_ngdu_id = CM_NGDU_ID_BY_ABAI.get(ngdu_abai_ids.get(brigade_id))
        ids = cm_ids.get((number, cm_ngdu_id))
        if ids:
            result[brigade_id] = ids
    return result
