"""Загрузка координат из ABAI и привязка их к скважинам.

Три шага за прогон:

1. ``emg.coord_system`` → ``wells_coord_system`` (справочник систем координат).
2. ``emg.spatial_object`` → ``wells_coord``, но не вся таблица, а только
   объекты, на которые ссылаются устья скважин (``well.whc``).
3. ``wells_well.coords_id`` := ``well.whc`` для тех объектов, что реально
   легли в ``wells_coord`` (поле — FK на ``wells_coord.abai_id``).

Координаты в ABAI лежат родными геометрическими типами Postgres (``point`` /
``polygon``), а у нас — PostGIS-геометрией, поэтому по дороге значения
переводятся в WKT: ``ST_GeomFromEWKT`` на нашей стороне разбирает его сам.

Таска идемпотентна: повторный прогон создаёт только новое, обновляет
изменившееся и перепривязывает только те скважины, у которых координата
поменялась. Забой (``well.bottom_coord``) не грузится — в нашей модели у
скважины одна координата, и это устье.
"""

import asyncio
import re
from collections.abc import Sequence

from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.coords import (
    CreateCoordDTO,
    CreateWellCoordDTO,
    UpdateCoordDTO,
    UpdateWellCoordDTO,
)
from apps.wells.models.coords import Coord
from apps.wells.repositories import (
    CoordRepository,
    WellCoordRepository,
    WellRepository,
)
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import CoordSystem as ABAICoordSystem
from shared.integrations.abai.models import SpatialObject as ABAISpatialObject
from shared.integrations.abai.repositories import (
    ABAICoordSystemRepository,
    ABAISpatialObjectRepository,
    ABAIWellRepository,
)
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class LoadCoords:
    # Размер IN-списка при вычитке пространственных объектов из источника.
    CHUNK_SIZE = 1000

    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.abai_coord_system_repo = ABAICoordSystemRepository(session=abai_session)
        self.abai_spatial_object_repo = ABAISpatialObjectRepository(
            session=abai_session,
        )
        self.abai_well_repo = ABAIWellRepository(session=abai_session)
        self.coord_repo = CoordRepository(session=app_session)
        self.well_coord_repo = WellCoordRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)
        self.app_session = app_session

    async def run(self) -> None:
        try:
            known_system_ids = await self._sync_coord_systems()
            whc_by_abai_well_id = await self._whc_by_abai_well_id()
            known_coord_ids = await self._sync_well_coords(
                sorted(
                    {whc for whc in whc_by_abai_well_id.values() if whc is not None},
                ),
                known_system_ids,
            )
            bound = await self._bind_wells(whc_by_abai_well_id, known_coord_ids)
            await self.app_session.commit()
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while loading coords")
            raise

        logger.info(
            "Coords sync finished: systems=%s, coords=%s, wells rebound=%s",
            len(known_system_ids),
            len(known_coord_ids),
            bound,
        )

    async def _sync_coord_systems(self) -> set[int]:
        source_systems = await self.abai_coord_system_repo.list_all()
        app_systems = {c.abai_id: c for c in await self.coord_repo.list_all()}

        new_systems = [s for s in source_systems if s.id not in app_systems]
        if new_systems:
            await self.coord_repo.bulk_create(
                [
                    CreateCoordDTO(
                        abai_id=s.id,
                        mn=s.mn,
                        name_ru=s.name_ru,
                        srid=s.srid,
                    )
                    for s in new_systems
                ],
            )

        updated = 0
        for source_system in source_systems:
            app_system = app_systems.get(source_system.id)
            if app_system is None or not self._is_system_changed(
                app_system,
                source_system,
            ):
                continue
            await self.coord_repo.update_by_abai_id(
                abai_id=source_system.id,
                data=UpdateCoordDTO(
                    mn=source_system.mn,
                    name_ru=source_system.name_ru,
                    srid=source_system.srid,
                ),
            )
            updated += 1

        logger.info(
            "Coord systems: source=%s, created=%s, updated=%s",
            len(source_systems),
            len(new_systems),
            updated,
        )
        return {s.id for s in source_systems} | set(app_systems)

    async def _whc_by_abai_well_id(self) -> dict[int, int | None]:
        """Устье каждой скважины источника: abai well id → spatial_object id."""
        source_wells = await self.abai_well_repo.get_list(spec=QuerySpec())
        return {well.id: well.whc for well in source_wells}

    async def _sync_well_coords(
        self,
        coord_ids: Sequence[int],
        known_system_ids: set[int],
    ) -> set[int]:
        app_coords = await self.well_coord_repo.list_wkt_by_abai_id()
        created = 0
        updated = 0
        orphan_system_ids: set[int] = set()

        for start in range(0, len(coord_ids), self.CHUNK_SIZE):
            chunk = coord_ids[start : start + self.CHUNK_SIZE]
            source_objects = await self.abai_spatial_object_repo.list_by_ids(chunk)

            new_objects: list[CreateWellCoordDTO] = []
            for source_object in source_objects:
                system_id = source_object.coord_system
                if system_id is not None and system_id not in known_system_ids:
                    orphan_system_ids.add(system_id)
                    system_id = None

                point_wkt = self._to_wkt(source_object.coord_point)
                polygon_wkt = self._to_wkt(source_object.coord_polygon)

                app_coord = app_coords.get(source_object.id)
                if app_coord is None:
                    new_objects.append(
                        CreateWellCoordDTO(
                            abai_id=source_object.id,
                            coords_system_id=system_id,
                            spatial_object_type=source_object.spatial_object_type,
                            coord_point=point_wkt,
                            coord_polygon=polygon_wkt,
                        ),
                    )
                    continue

                if not self._is_coord_changed(
                    app_coord,
                    source_object,
                    system_id,
                    point_wkt=point_wkt,
                    polygon_wkt=polygon_wkt,
                ):
                    continue

                await self.well_coord_repo.update_by_abai_id(
                    abai_id=source_object.id,
                    data=UpdateWellCoordDTO(
                        coords_system_id=system_id,
                        spatial_object_type=source_object.spatial_object_type,
                        coord_point=point_wkt,
                        coord_polygon=polygon_wkt,
                    ),
                )
                updated += 1

            if new_objects:
                await self.well_coord_repo.bulk_create(new_objects)
                created += len(new_objects)
                app_coords.update({dto.abai_id: None for dto in new_objects})  # type: ignore[misc]

        if orphan_system_ids:
            logger.warning(
                "Spatial objects reference unknown coord systems (stored as NULL): %s",
                sorted(orphan_system_ids),
            )

        logger.info(
            "Well coords: requested=%s, created=%s, updated=%s",
            len(coord_ids),
            created,
            updated,
        )
        return set(app_coords)

    async def _bind_wells(
        self,
        whc_by_abai_well_id: dict[int, int | None],
        known_coord_ids: set[int],
    ) -> int:
        app_wells = await self.well_repo.get_list()
        changes: dict[int, int | None] = {}

        for well in app_wells:
            whc = whc_by_abai_well_id.get(well.abai_id)
            coords_id = whc if whc in known_coord_ids else None
            if coords_id != well.coords_id:
                changes[well.abai_id] = coords_id

        await self.well_repo.update_coords_by_abai_ids(changes)
        return len(changes)

    @staticmethod
    def _is_system_changed(app_system: Coord, source_system: ABAICoordSystem) -> bool:
        return (
            app_system.mn != source_system.mn
            or app_system.name_ru != source_system.name_ru
            or app_system.srid != source_system.srid
        )

    @classmethod
    def _is_coord_changed(
        cls,
        app_coord: RowMapping,
        source_object: ABAISpatialObject,
        system_id: int | None,
        *,
        point_wkt: str | None,
        polygon_wkt: str | None,
    ) -> bool:
        return (
            app_coord["coords_system_id"] != system_id
            or app_coord["spatial_object_type"] != source_object.spatial_object_type
            or cls._normalize_wkt(app_coord["coord_point_wkt"])
            != cls._normalize_wkt(point_wkt)
            or cls._normalize_wkt(app_coord["coord_polygon_wkt"])
            != cls._normalize_wkt(polygon_wkt)
        )

    @staticmethod
    def _normalize_wkt(value: str | None) -> str | None:
        """Схлопывает пробелы после запятых — форматирование не должно
        считаться изменением геометрии."""
        return None if value is None else value.replace(", ", ",")

    @classmethod
    def _to_wkt(cls, value: object) -> str | None:
        """Родная геометрия Postgres (``asyncpg.Point`` / ``Polygon``) → WKT.

        Строку из источника отдаём как есть — это уже либо WKT, либо
        postgres-текст вида ``(x,y)``, который тоже разбираем.
        """
        if value is None:
            return None
        if isinstance(value, str):
            return cls._parse_pg_text(value)

        x = getattr(value, "x", None)
        y = getattr(value, "y", None)
        if x is not None and y is not None:
            return f"POINT({cls._fmt(x)} {cls._fmt(y)})"

        try:
            points = [(float(p[0]), float(p[1])) for p in value]  # type: ignore[index]
        except (TypeError, ValueError, IndexError):
            logger.warning("Unsupported geometry value: %r", value)
            return None

        return cls._ring_to_wkt(points)

    @classmethod
    def _parse_pg_text(cls, value: str) -> str | None:
        """``(x,y)`` → POINT, ``((x1,y1),(x2,y2),...)`` → POLYGON."""
        text = value.strip()
        if not text:
            return None
        if text.upper().startswith(("POINT", "POLYGON", "SRID=")):
            return text

        pairs = re.findall(r"\(\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)\s*\)", text)
        if not pairs:
            logger.warning("Unsupported geometry text: %r", value)
            return None
        points = [(float(x), float(y)) for x, y in pairs]
        if len(points) == 1:
            x, y = points[0]
            return f"POINT({cls._fmt(x)} {cls._fmt(y)})"
        return cls._ring_to_wkt(points)

    # Кольцо полигона в PostGIS — минимум 3 разные вершины (+ замыкающая).
    MIN_RING_POINTS = 3

    @classmethod
    def _ring_to_wkt(cls, points: list[tuple[float, float]]) -> str | None:
        if not points:
            return None

        unique = {*points}
        if len(unique) < cls.MIN_RING_POINTS:
            logger.warning("Degenerate polygon skipped: %r", points)
            return None

        ring = [*points, points[0]] if points[0] != points[-1] else list(points)
        # Без пробела после запятой — ровно как печатает ST_AsText, иначе
        # сравнение с сохранённым значением всегда будет считать строку
        # изменившейся.
        body = ",".join(f"{cls._fmt(x)} {cls._fmt(y)}" for x, y in ring)
        return f"POLYGON(({body}))"

    @staticmethod
    def _fmt(value: float) -> str:
        """Как печатает координату PostGIS: без хвостового ``.0``."""
        text = repr(float(value))
        return text.removesuffix(".0")


async def main() -> None:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        await LoadCoords(
            abai_session=abai_session,
            app_session=app_session,
        ).run()


if __name__ == "__main__":
    asyncio.run(main())
