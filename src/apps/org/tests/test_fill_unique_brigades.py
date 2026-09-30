"""Уникальные бригады: орган бригады — ABAI-id, лишние без ремонтов удаляются."""

import asyncio
from types import SimpleNamespace

import pytest

from apps.org.tasks.fill_unique_brigades import fill_unique_brigades as module

NGDU = 10  # org_type_id НГДУ
# Локальный id 5 у Кайнара совпал с ABAI-id брошенного органа 5 — на этом и
# ломалось сопоставление по org.id.
ORGS = [
    SimpleNamespace(id=4, abai_id=11, org_type_id=NGDU),  # Жайык
    SimpleNamespace(id=5, abai_id=12, org_type_id=NGDU),  # Кайнар
]
BRIGADES = [
    SimpleNamespace(name_ru="Бригада №3", org_id=11),
    SimpleNamespace(name_ru="Бригада №3", org_id=12),
    SimpleNamespace(name_ru="Кезби-3", org_id=5),  # брошенный орган ABAI
]
EXISTING = [
    SimpleNamespace(id=23, name="Бригада №3", ngdu_id=5),  # останется
    SimpleNamespace(id=45, name="Кезби-3", ngdu_id=5),  # лишняя, без ремонтов
    SimpleNamespace(id=46, name="ММС-3", ngdu_id=5),  # лишняя, но с ремонтом
]


class _Session:
    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> "_Session":  # noqa: PYI034
        return self

    async def __aexit__(self, *_exc: object) -> None:
        pass


class _Repo:
    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.created: list = []
        self.deleted: list[int] = []

    async def get_list(self, **_kw: object) -> list:
        return self.rows

    async def list_all(self) -> list:
        return self.rows

    async def bulk_create(self, data: list) -> None:
        self.created.extend((d.name, d.ngdu_id) for d in data)

    async def delete_by_id(self, row_id: int) -> None:
        self.deleted.append(row_id)


class _Links:
    async def list_by_brigade_ids(self, ids: list[int]) -> list:
        return [SimpleNamespace(brigade_id=46)] if 46 in ids else []


def test_brigades_by_abai_org_and_stale_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unique = _Repo(EXISTING)
    monkeypatch.setattr(module, "session_makers", {"app": _Session})
    monkeypatch.setattr(module, "BrigadeRepository", lambda _s: _Repo(BRIGADES))
    monkeypatch.setattr(module, "OrgRepository", lambda _s: _Repo(ORGS))
    monkeypatch.setattr(module, "UniqueBrigadeRepository", lambda _s: unique)
    monkeypatch.setattr(module, "RepairBrigadeRepository", lambda _s: _Links())

    asyncio.run(module.FillUniqueBrigades().run())

    assert unique.created == [("Бригада №3", 4)]  # у Жайыка появилась своя №3
    assert unique.deleted == [45]  # Кезби-3 удалена, ММС-3 с ремонтом оставлена
