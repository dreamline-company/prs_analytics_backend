"""Скважины, которые сейчас числятся за НГДУ.

Цепочка разрешения (общая для матрицы скважин и её производных):
  1. Взять ``Org`` НГДУ по локальному id → собрать все дочерние орг. объекты
     по ``parent_id`` (он хранит ABAI id).
  2. По локальному зеркалу ``wells_well_org`` найти скважины, чья текущая
     привязка (последняя по ``dbeg``, tiebreak по ``abai_id``) лежит внутри
     поддерева НГДУ. Скважины, переведённые в другое подразделение, отсеиваются
     на стороне БД.
  3. Отдать неудалённые скважины из ``wells_well``.
  4. При фильтре по месторождению оставить скважины с его префиксом имени
     (месторождение = префикс до подчёркивания, см. ``oil_fields``).

Без НГДУ берутся скважины всех НГДУ разом (объединение их поддеревьев) — это
тот же набор, что сумма по отдельным НГДУ, поэтому общий итог сводки сходится
с итогами по НГДУ.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from starlette import status

from apps.org.services import wells_with_prefix
from shared.constants.ngdu import NGDU_ORG_TYPE
from shared.errors import HttpError

if TYPE_CHECKING:
    from apps.org.models.org import Org
    from apps.org.repositories.oil_field import OilFieldRepository
    from apps.org.repositories.org import OrgRepository
    from apps.wells.models.well import Well
    from apps.wells.repositories.well import WellRepository
    from apps.wells.repositories.well_org import WellOrgRepository


class OilFieldNotFoundError(HttpError):
    """Месторождения нет или оно относится к другому НГДУ."""

    message = "Oil field not found in this NGDU."
    code = "oil_field_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class NGDUWellsService:
    def __init__(
        self,
        *,
        org_repository: OrgRepository,
        well_repository: WellRepository,
        well_org_repository: WellOrgRepository,
        oil_field_repository: OilFieldRepository,
    ) -> None:
        self.org_repository = org_repository
        self.well_repository = well_repository
        self.well_org_repository = well_org_repository
        self.oil_field_repository = oil_field_repository

    async def list_wells(
        self,
        ngdu_id: int | None = None,
        *,
        oil_field_id: int | None = None,
    ) -> list[Well]:
        """Неудалённые скважины по фильтрам; оба необязательны.

        Без фильтров — скважины всех НГДУ. ``ngdu_id`` — один НГДУ (пустой
        список, если его нет или он пуст). ``oil_field_id`` сужает список до
        скважин месторождения; НГДУ при этом берётся из самого месторождения,
        а если ``ngdu_id`` тоже задан, они должны совпадать: один префикс может
        числиться за несколькими НГДУ. Несуществующее или чужое месторождение
        — 404; проверяется до разрешения скважин, чтобы ошибка не зависела от
        наполнения зеркала.
        """
        prefix: str | None = None
        if oil_field_id is not None:
            oil_field = await self.oil_field_repository.get_by_id(oil_field_id)
            if oil_field is None or (
                ngdu_id is not None and oil_field.ngdu_id != ngdu_id
            ):
                raise OilFieldNotFoundError(
                    details={"oil_field_id": oil_field_id, "ngdu_id": ngdu_id},
                )
            ngdu_id = oil_field.ngdu_id
            prefix = oil_field.prefix

        if ngdu_id is None:
            wells = await self._list_all_ngdu_wells()
        else:
            wells = await self._list_ngdu_wells(ngdu_id)
        if prefix is not None:
            wells = wells_with_prefix(wells, prefix)
        return wells

    async def _list_ngdu_wells(self, ngdu_id: int) -> list[Well]:
        ngdu = await self.org_repository.get_by_id(ngdu_id)
        if ngdu is None:
            return []
        children_by_parent = await self._children_by_parent()
        return await self._list_wells_of_subtrees(
            self._subtree_abai_ids(ngdu.abai_id, children_by_parent),
        )

    async def _list_all_ngdu_wells(self) -> list[Well]:
        """Скважины всех НГДУ: объединение поддеревьев организаций типа НГДУ."""
        children_by_parent = await self._children_by_parent()
        all_orgs = await self.org_repository.list_all()
        abai_ids: set[int] = set()
        for org in all_orgs:
            if org.org_type_id == NGDU_ORG_TYPE:
                abai_ids |= self._subtree_abai_ids(org.abai_id, children_by_parent)
        return await self._list_wells_of_subtrees(abai_ids)

    async def _list_wells_of_subtrees(self, org_abai_ids: set[int]) -> list[Well]:
        if not org_abai_ids:
            return []
        well_abai_ids = (
            await self.well_org_repository.list_current_abai_well_ids_by_abai_org_ids(
                sorted(org_abai_ids),
            )
        )
        if not well_abai_ids:
            return []
        wells = await self.well_repository.list_by_abai_ids(well_abai_ids)
        return [well for well in wells if not well.is_deleted]

    async def _children_by_parent(self) -> dict[int, list[Org]]:
        """Дети по ABAI id родителя — ``parent_id`` в ``org`` хранит ABAI id."""
        children_by_parent: dict[int, list[Org]] = defaultdict(list)
        for org in await self.org_repository.list_all():
            if org.parent_id is not None:
                children_by_parent[org.parent_id].append(org)
        return children_by_parent

    @staticmethod
    def _subtree_abai_ids(
        root_abai_id: int,
        children_by_parent: dict[int, list[Org]],
    ) -> set[int]:
        result: set[int] = {root_abai_id}
        stack: list[int] = [root_abai_id]
        while stack:
            parent_abai_id = stack.pop()
            for child in children_by_parent.get(parent_abai_id, ()):
                if child.abai_id in result:
                    continue
                result.add(child.abai_id)
                stack.append(child.abai_id)
        return result
