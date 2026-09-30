"""Список НГДУ для ПРС — только Кайнармунайгаз и Жылыоймунайгаз; общий не меняется."""

import asyncio
from types import SimpleNamespace

from apps.org.use_cases.list_ngdus import PRS_NGDU_ABAI_IDS, ListNGDUsUseCase
from shared.constants.ngdu import AbaiNGDUIDsEnum

ORGS = {
    AbaiNGDUIDsEnum.DMG: SimpleNamespace(id=2, name_ru="Доссормунайгаз"),
    AbaiNGDUIDsEnum.ZHlMG: SimpleNamespace(id=3, name_ru="Жылыоймунайгаз"),
    AbaiNGDUIDsEnum.ZHMG: SimpleNamespace(id=4, name_ru="Жайкмунайгаз"),
    AbaiNGDUIDsEnum.KMG: SimpleNamespace(id=5, name_ru="Кайнармунайгаз"),
}


class _Orgs:
    async def list_by_abai_ids(self, ids: list[int]) -> list:
        return [ORGS[i] for i in sorted(ids)]


def _names(**kwargs: object) -> list[str]:
    use_case = ListNGDUsUseCase(org_repository=_Orgs(), **kwargs)  # type: ignore[arg-type]
    return [ngdu.name for ngdu in asyncio.run(use_case.execute())]


def test_prs_list_is_kmg_and_zhylyoi_only() -> None:
    assert _names(abai_ids=PRS_NGDU_ABAI_IDS) == ["Жылыоймунайгаз", "Кайнармунайгаз"]


def test_general_list_unchanged() -> None:
    assert _names() == ["Жайкмунайгаз", "Кайнармунайгаз"]
