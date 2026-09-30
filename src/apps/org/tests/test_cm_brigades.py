"""Бригада -> бригады CM: тот же номер в том же НГДУ."""

import asyncio
from types import SimpleNamespace

from apps.org.services.cm_brigades import match_cm_brigades
from shared.constants.ngdu import AbaiNGDUIDsEnum

BRIGADES = [
    SimpleNamespace(id=1, name="Бригада №3"),  # Кайнар
    SimpleNamespace(id=2, name="Бригада №3"),  # Жайык
    SimpleNamespace(id=3, name="Кезби-3"),  # без «№» — не бригада ПРС
]
NGDU_ABAI = {1: AbaiNGDUIDsEnum.KMG, 2: AbaiNGDUIDsEnum.ZHMG, 3: AbaiNGDUIDsEnum.KMG}
CM = [  # main_brigade: номер 3 есть во всех НГДУ CM
    SimpleNamespace(id=10, name="3", ngdu_id=1),  # Кайнар
    SimpleNamespace(id=40, name="3", ngdu_id=4),  # Жайык
    SimpleNamespace(id=20, name="3", ngdu_id=2),  # Жылыой
]


class _Brigades:
    async def map_ngdu_abai_ids(self, ids: list[int]) -> dict[int, int]:
        return {i: NGDU_ABAI[i] for i in ids}


class _CM:
    async def list_by_names(self, names: list[str]) -> list:
        return [cm for cm in CM if cm.name in names]


def test_same_number_in_own_ngdu_only() -> None:
    matched = asyncio.run(
        match_cm_brigades(
            BRIGADES,  # type: ignore[arg-type]
            unique_brigade_repo=_Brigades(),  # type: ignore[arg-type]
            cm_brigade_repo=_CM(),  # type: ignore[arg-type]
        ),
    )

    assert matched == {1: [10], 2: [40]}
