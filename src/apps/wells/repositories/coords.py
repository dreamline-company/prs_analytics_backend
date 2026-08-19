from collections.abc import Sequence

from sqlalchemy import RowMapping, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from apps.wells.dto.internal.repositories.coords import (
    CreateCoordDTO,
    CreateWellCoordDTO,
    UpdateCoordDTO,
    UpdateWellCoordDTO,
)
from apps.wells.models.coords import Coord, WellCoord
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class CoordRepository(
    AsyncAlchemyRepository[CreateCoordDTO, UpdateCoordDTO, Coord],
):
    model = Coord

    async def get_by_abai_id(self, abai_id: int) -> Coord | None:
        return await self.get_one(
            QuerySpec(
                filters=(Coord.abai_id == abai_id,),
            ),
        )

    async def get_by_mn(self, mn: str) -> Coord | None:
        return await self.get_one(
            QuerySpec(
                filters=(Coord.mn == mn,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[Coord]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Coord.abai_id.in_(abai_ids),),
                order_by=(Coord.abai_id,),
            ),
        )

    async def list_all(self) -> Sequence[Coord]:
        return await self.get_list(QuerySpec(order_by=(Coord.abai_id,)))

    async def update_by_abai_id(self, abai_id: int, data: UpdateCoordDTO) -> Coord:
        return await self.update(
            data=data,
            filters=(Coord.abai_id == abai_id,),
        )

    async def update_by_id(self, coord_id: int, data: UpdateCoordDTO) -> Coord:
        return await self.update(
            data=data,
            filters=(Coord.id == coord_id,),
        )

    async def delete_by_id(self, coord_id: int) -> None:
        await self.delete(filters=(Coord.id == coord_id,))


class WellCoordRepository(
    AsyncAlchemyRepository[CreateWellCoordDTO, UpdateWellCoordDTO, WellCoord],
):
    model = WellCoord

    async def get_by_abai_id(self, abai_id: int) -> WellCoord | None:
        return await self.get_one(
            QuerySpec(
                filters=(WellCoord.abai_id == abai_id,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[WellCoord]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellCoord.abai_id.in_(abai_ids),),
                order_by=(WellCoord.abai_id,),
            ),
        )

    async def list_all(self) -> Sequence[WellCoord]:
        return await self.get_list(QuerySpec(order_by=(WellCoord.abai_id,)))

    async def list_wkt_by_abai_id(self) -> dict[int, RowMapping]:
        """Все координаты с геометрией в WKT — для сравнения с источником.

        Геометрию сравнивать в виде WKB-хеша нельзя: из ABAI приходит родной
        postgres-тип, а у нас PostGIS. Текстовое представление — общий
        знаменатель.
        """
        stmt = select(
            WellCoord.abai_id,
            WellCoord.coords_system_id,
            WellCoord.spatial_object_type,
            func.ST_AsText(WellCoord.coord_point).label("coord_point_wkt"),
            func.ST_AsText(WellCoord.coord_polygon).label("coord_polygon_wkt"),
        )
        result = await self.session.execute(stmt)
        return {row["abai_id"]: row for row in result.mappings().all()}

    async def list_map_points(
        self,
        well_ids: Sequence[int] | None = None,
    ) -> Sequence[RowMapping]:
        """Отрисовываемые устья одним запросом: well_id, name, lat, lon.

        ``well_ids`` ограничивает выборку (например, скважинами НГДУ); ``None``
        — вся база. Фильтр через ``any(array)``, а не ``IN``: список может быть
        на тысячи скважин.

        Классификация координат (та же, что в ``CoordPointService``) вынесена
        в materialized CTE намеренно: ``ST_Transform`` бросает
        ``Invalid coordinate`` на мусорных значениях и рвёт транзакцию, поэтому
        он должен видеть только уже отфильтрованные строки. Отсеиваются нули,
        координаты, не соответствующие своей системе, и абсурдные величины.
        """
        stmt = text("""
            with mappable as materialized (
                select
                    w.id                as well_id,
                    w.name              as well_name,
                    cs.srid             as srid,
                    ST_X(c.coord_point) as x,
                    ST_Y(c.coord_point) as y
                from wells_well w
                join wells_coord c on c.abai_id = w.coords_id
                left join wells_coord_system cs on cs.abai_id = c.coords_system_id
                where w.is_deleted is false
                  and c.coord_point is not null
                  and (
                      cast(:well_ids as bigint[]) is null
                      or w.id = any(cast(:well_ids as bigint[]))
                  )
                  and not (ST_X(c.coord_point) = 0 and ST_Y(c.coord_point) = 0)
                  and case
                      -- Географическая система обязана нести градусы.
                      when cs.srid is null or cs.srid in (4326, 4284) then
                          abs(ST_X(c.coord_point)) <= 180
                          and abs(ST_Y(c.coord_point)) <= 90
                      -- Проекционная — метры, и в пределах разумного.
                      else
                          not (
                              abs(ST_X(c.coord_point)) <= 180
                              and abs(ST_Y(c.coord_point)) <= 90
                          )
                          and abs(ST_X(c.coord_point)) <= 20000000
                          and abs(ST_Y(c.coord_point)) <= 20000000
                  end
            )
            select
                well_id,
                well_name,
                case
                    when srid is null or srid = 4326 then y
                    else ST_Y(
                        ST_Transform(ST_SetSRID(ST_MakePoint(x, y), srid), 4326)
                    )
                end as lat,
                case
                    when srid is null or srid = 4326 then x
                    else ST_X(
                        ST_Transform(ST_SetSRID(ST_MakePoint(x, y), srid), 4326)
                    )
                end as lon
            from mappable
            order by well_name
        """)
        result = await self.session.execute(
            stmt,
            {"well_ids": list(well_ids) if well_ids is not None else None},
        )
        return result.mappings().all()

    async def get_point_by_abai_id(self, abai_id: int) -> RowMapping | None:
        """Координата вместе с её системой — сырые x/y как в источнике."""
        stmt = (
            select(
                WellCoord.abai_id,
                func.ST_X(WellCoord.coord_point).label("x"),
                func.ST_Y(WellCoord.coord_point).label("y"),
                Coord.srid.label("srid"),
                Coord.mn.label("system"),
                Coord.name_ru.label("system_name"),
            )
            .join(Coord, Coord.abai_id == WellCoord.coords_system_id, isouter=True)
            .where(WellCoord.abai_id == abai_id)
        )
        result = await self.session.execute(stmt)
        return result.mappings().one_or_none()

    async def transform_to_wgs84(
        self,
        *,
        x: float,
        y: float,
        srid: int,
    ) -> tuple[float, float] | None:
        """(x, y, srid) → (lon, lat) в WGS 84; None, если PostGIS не смог.

        ST_Transform на мусорных значениях бросает ``Invalid coordinate`` и
        рвёт транзакцию, поэтому вызов идёт в сейвпоинте.
        """
        savepoint = await self.session.begin_nested()
        try:
            row = (
                (
                    await self.session.execute(
                        text(
                            "select ST_X(p) as lon, ST_Y(p) as lat from ("
                            "select ST_Transform("
                            "ST_SetSRID(ST_MakePoint(:x, :y), :srid), 4326"
                            ") as p) t",
                        ),
                        {"x": x, "y": y, "srid": srid},
                    )
                )
                .mappings()
                .one()
            )
        except SQLAlchemyError:
            await savepoint.rollback()
            return None

        await savepoint.commit()
        return row["lon"], row["lat"]

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellCoordDTO,
    ) -> WellCoord:
        return await self.update(
            data=data,
            filters=(WellCoord.abai_id == abai_id,),
        )

    async def list_by_coords_system_id(
        self,
        coords_system_id: int,
    ) -> Sequence[WellCoord]:
        return await self.get_list(
            QuerySpec(
                filters=(WellCoord.coords_system_id == coords_system_id,),
                order_by=(WellCoord.abai_id,),
            ),
        )

    async def update_by_id(
        self,
        well_coord_id: int,
        data: UpdateWellCoordDTO,
    ) -> WellCoord:
        return await self.update(
            data=data,
            filters=(WellCoord.id == well_coord_id,),
        )

    async def delete_by_id(self, well_coord_id: int) -> None:
        await self.delete(filters=(WellCoord.id == well_coord_id,))
