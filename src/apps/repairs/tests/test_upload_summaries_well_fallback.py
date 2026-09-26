"""Запасное разрешение имён скважин из сводок: регистр суффикса и суффикс-пометка."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.repairs.use_cases.upload_parsed_summaries import UploadParsedSummariesUseCase

WELLS = {
    "SKS_004p": SimpleNamespace(id=1, name="SKS_004p", abai_id=1001),
    "KRK_0179": SimpleNamespace(id=2, name="KRK_0179", abai_id=1002),
    "DSR_01/1": SimpleNamespace(id=3, name="DSR_01/1", abai_id=1003),
    "BLG_101A": SimpleNamespace(id=4, name="BLG_101A", abai_id=1004),
}


class _Wells:
    async def list_by_names(self, names: list[str]) -> list:
        return [WELLS[n] for n in names if n in WELLS]

    async def list_by_names_ci(self, names: list[str]) -> list:
        wanted = {n.lower() for n in names}
        return [w for w in WELLS.values() if w.name.lower() in wanted]


def _use_case() -> UploadParsedSummariesUseCase:
    return UploadParsedSummariesUseCase(
        session=None,  # type: ignore[arg-type]
        well_repository=_Wells(),  # type: ignore[arg-type]
        repair_summary_repository=None,  # type: ignore[arg-type]
        repair_repository=None,  # type: ignore[arg-type]
        repair_brigade_repository=None,  # type: ignore[arg-type]
        unique_brigade_repository=None,  # type: ignore[arg-type]
    )


def test_fallback_resolves_case_suffix_and_slash_names() -> None:
    names = {"SKS_004P", "KRK_179V", "DSR_1/1", "BLG_101A", "XXX_0001"}

    resolved = asyncio.run(_use_case()._resolve_fallback(names))  # noqa: SLF001

    assert {k: v.name for k, v in resolved.items()} == {
        "SKS_004P": "SKS_004p",  # регистр суффикса
        "KRK_179V": "KRK_0179",  # суффикс-пометка -> имя без суффикса
        "DSR_1/1": "DSR_01/1",  # дробь с нулём
        "BLG_101A": "BLG_101A",  # точное имя через регистронезависимый поиск
    }
    assert "XXX_0001" not in resolved
