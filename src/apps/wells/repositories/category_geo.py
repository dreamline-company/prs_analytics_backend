from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select

from apps.wells.dto.internal.repositories.category_geo import (
    GeoDTO,
    GeoTypeDTO,
    WellCategoryDTO,
    WellCategoryTypeDTO,
    WellGeoDTO,
)
from apps.wells.models.category_geo import (
    Geo,
    GeoType,
    WellCategory,
    WellCategoryType,
    WellGeo,
)
from apps.wells.repositories.abai_mirror import AbaiMirrorRepository


class WellCategoryTypeRepository(
    AbaiMirrorRepository[WellCategoryTypeDTO, WellCategoryType],
):
    model = WellCategoryType


class WellCategoryRepository(AbaiMirrorRepository[WellCategoryDTO, WellCategory]):
    model = WellCategory

    async def list_intervals_by_code(
        self,
        abai_well_ids: Sequence[int],
        *,
        code: str,
        since: datetime,
        until: datetime,
    ) -> list[tuple[int, datetime | None, datetime]]:
        """Периоды категории ``code`` у скважин, пересекающие [since, until].

        Строка: (abai_well_id, dbeg, dend). ``dbeg`` NULL — начало неизвестно
        (с незапамятных времён), остаётся как есть; период с ``dend`` NULL
        закрыт неизвестно когда и на даты не действует — не отдаётся.
        """
        if not abai_well_ids:
            return []

        stmt = (
            select(WellCategory.abai_well_id, WellCategory.dbeg, WellCategory.dend)
            .join(
                WellCategoryType,
                WellCategoryType.abai_id == WellCategory.abai_category_type_id,
            )
            .where(
                WellCategory.abai_well_id.in_(abai_well_ids),
                WellCategoryType.code == code,
                WellCategory.dend.is_not(None),
                WellCategory.dend > since,
                (WellCategory.dbeg.is_(None)) | (WellCategory.dbeg <= until),
            )
            .order_by(WellCategory.abai_well_id, WellCategory.dbeg)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in result.all()]


class GeoTypeRepository(AbaiMirrorRepository[GeoTypeDTO, GeoType]):
    model = GeoType


class GeoRepository(AbaiMirrorRepository[GeoDTO, Geo]):
    model = Geo


class WellGeoRepository(AbaiMirrorRepository[WellGeoDTO, WellGeo]):
    model = WellGeo

    HORIZON_TYPE_CODE = "HRZ"

    async def list_horizon_names_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
        *,
        on_date: date,
    ) -> dict[int, list[str]]:
        """Названия горизонтов скважин, действующих на дату.

        Действует на дату привязка с ``dbeg <= d < dend``; ``dbeg`` NULL — с
        незапамятных времён, ``dend`` NULL — период не действует. У скважины
        горизонтов бывает несколько, а в источнике есть дубли и пересечения,
        поэтому берутся уникальные ``abai_geo_id``, а одинаковые названия под
        разными id схлопываются. Порядок — по названию.
        """
        if not abai_well_ids:
            return {}

        stmt = (
            select(
                WellGeo.abai_well_id,
                func.coalesce(Geo.name_ru, Geo.name_short_ru),
            )
            .join(Geo, Geo.abai_id == WellGeo.abai_geo_id)
            .join(GeoType, GeoType.abai_id == Geo.abai_geo_type_id)
            .where(
                WellGeo.abai_well_id.in_(abai_well_ids),
                GeoType.code == self.HORIZON_TYPE_CODE,
                WellGeo.dend.is_not(None),
                WellGeo.dend > on_date,
                (WellGeo.dbeg.is_(None)) | (WellGeo.dbeg <= on_date),
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        names: dict[int, set[str]] = defaultdict(set)
        for abai_well_id, name in result.all():
            if name:
                names[abai_well_id].add(name)
        return {well_id: sorted(values) for well_id, values in names.items()}
