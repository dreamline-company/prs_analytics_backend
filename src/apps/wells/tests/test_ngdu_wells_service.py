"""Разрешение скважин по фильтрам НГДУ / месторождение на фейковых репозиториях."""

import asyncio
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

import pytest

from apps.wells.services.ngdu_wells import NGDUWellsService, OilFieldNotFoundError
from shared.constants.ngdu import NGDU_ORG_TYPE

# org: НГДУ A (abai 100) с цехом 110, НГДУ B (abai 200); посторонняя организация 300.
ORGS = [
    SimpleNamespace(id=1, abai_id=100, parent_id=None, org_type_id=NGDU_ORG_TYPE),
    SimpleNamespace(id=11, abai_id=110, parent_id=100, org_type_id=1),
    SimpleNamespace(id=2, abai_id=200, parent_id=None, org_type_id=NGDU_ORG_TYPE),
    SimpleNamespace(id=3, abai_id=300, parent_id=None, org_type_id=1),
]
# текущая привязка: abai_well_id -> abai_org_id
BINDINGS = {1001: 110, 1002: 100, 2001: 200, 3001: 300}
WELLS = {
    1001: SimpleNamespace(id=1, abai_id=1001, name="BLG_0001", is_deleted=False),
    1002: SimpleNamespace(id=2, abai_id=1002, name="UZK_0002", is_deleted=False),
    2001: SimpleNamespace(id=3, abai_id=2001, name="BLG_0003", is_deleted=False),
    3001: SimpleNamespace(id=4, abai_id=3001, name="XXX_0004", is_deleted=False),
}
OIL_FIELDS = {
    7: SimpleNamespace(id=7, prefix="BLG", ngdu_id=1),
    8: SimpleNamespace(id=8, prefix="BLG", ngdu_id=2),
}


class _Orgs:
    async def get_by_id(self, org_id: int) -> SimpleNamespace | None:
        return next((o for o in ORGS if o.id == org_id), None)

    async def list_all(self) -> list[SimpleNamespace]:
        return ORGS


class _WellOrgs:
    async def list_current_abai_well_ids_by_abai_org_ids(
        self,
        abai_org_ids: list[int],
    ) -> list[int]:
        wanted = set(abai_org_ids)
        return sorted(w for w, org in BINDINGS.items() if org in wanted)


class _Wells:
    async def list_by_abai_ids(self, abai_ids: list[int]) -> list[SimpleNamespace]:
        return [WELLS[i] for i in abai_ids]


class _OilFields:
    async def get_by_id(self, oil_field_id: int) -> SimpleNamespace | None:
        return OIL_FIELDS.get(oil_field_id)


def _service() -> NGDUWellsService:
    return NGDUWellsService(
        org_repository=_Orgs(),  # type: ignore[arg-type]
        well_repository=_Wells(),  # type: ignore[arg-type]
        well_org_repository=_WellOrgs(),  # type: ignore[arg-type]
        oil_field_repository=_OilFields(),  # type: ignore[arg-type]
    )


def _names(coro: Coroutine[Any, Any, list]) -> list[str]:
    return sorted(w.name for w in asyncio.run(coro))


def test_no_filters_returns_wells_of_all_ngdus_only() -> None:
    # XXX_0004 привязана к организации вне НГДУ — в «все НГДУ» не входит.
    assert _names(_service().list_wells()) == ["BLG_0001", "BLG_0003", "UZK_0002"]


def test_ngdu_filter_includes_child_orgs() -> None:
    assert _names(_service().list_wells(1)) == ["BLG_0001", "UZK_0002"]
    assert _names(_service().list_wells(2)) == ["BLG_0003"]
    assert asyncio.run(_service().list_wells(999)) == []


def test_oil_field_alone_implies_its_ngdu() -> None:
    # Один префикс BLG заведён для двух НГДУ — берутся скважины НГДУ месторождения.
    assert _names(_service().list_wells(oil_field_id=7)) == ["BLG_0001"]
    assert _names(_service().list_wells(oil_field_id=8)) == ["BLG_0003"]
    assert _names(_service().list_wells(1, oil_field_id=7)) == ["BLG_0001"]


def test_oil_field_of_other_ngdu_or_unknown_is_404() -> None:
    with pytest.raises(OilFieldNotFoundError):
        asyncio.run(_service().list_wells(1, oil_field_id=8))
    with pytest.raises(OilFieldNotFoundError):
        asyncio.run(_service().list_wells(oil_field_id=999))
