from collections.abc import Sequence
from datetime import date

from sqlalchemy import BigInteger, cast, func, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.wells.dto.internal.repositories.gdis import (
    CreateGdisCurrentDTO,
    CreateGdisCurrentValueDTO,
    CreateGdisMetricDTO,
    UpdateGdisCurrentDTO,
    UpdateGdisCurrentValueDTO,
    UpdateGdisMetricDTO,
)
from apps.wells.models.gdis import GdisCurrent, GdisCurrentValue, GdisMetric
from shared.dto.repositories import RepositoryDTO
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec

# Ключ каждого зеркала — ABAI id; при повторе строки обновляются все поля,
# кроме самого ключа: источник правит записи на месте, без отметки времени.

# asyncpg не принимает больше 32 767 параметров в одном запросе; многострочный
# INSERT ... VALUES режется так, чтобы строк × колонок не выходило за лимит.
MAX_QUERY_PARAMS = 30_000


def rows_per_statement(columns: int) -> int:
    return max(1, MAX_QUERY_PARAMS // max(columns, 1))


async def _upsert_by_abai_id(
    repository: AsyncAlchemyRepository,
    data: Sequence[RepositoryDTO],
) -> None:
    if not data:
        return
    by_key = {item.abai_id: item for item in data}  # type: ignore[attr-defined]
    rows = [item.model_dump() for item in by_key.values()]
    step = rows_per_statement(len(rows[0]))
    for start in range(0, len(rows), step):
        stmt = pg_insert(repository.model).values(rows[start : start + step])
        mutable = [c.name for c in stmt.excluded if c.name not in ("id", "abai_id")]
        stmt = stmt.on_conflict_do_update(
            index_elements=["abai_id"],
            set_={column: getattr(stmt.excluded, column) for column in mutable},
        )
        await repository.session.execute(stmt)


class GdisMetricRepository(
    AsyncAlchemyRepository[CreateGdisMetricDTO, UpdateGdisMetricDTO, GdisMetric],
):
    model = GdisMetric

    async def list_abai_ids(self) -> set[int]:
        result = await self.session.execute(select(GdisMetric.abai_id))
        return set(result.scalars().all())

    async def list_all(self) -> Sequence[GdisMetric]:
        return await self.get_list(QuerySpec(order_by=(GdisMetric.abai_id,)))

    async def list_abai_ids_by_names(self, names: Sequence[str]) -> set[int]:
        """ABAI id метрик по точному ``name_ru`` (одна величина может быть
        заведена под несколькими именами)."""
        if not names:
            return set()
        result = await self.session.execute(
            select(GdisMetric.abai_id).where(GdisMetric.name_ru.in_(names)),
        )
        return set(result.scalars().all())

    async def upsert_many(self, data: Sequence[CreateGdisMetricDTO]) -> None:
        await _upsert_by_abai_id(self, data)


class GdisCurrentRepository(
    AsyncAlchemyRepository[CreateGdisCurrentDTO, UpdateGdisCurrentDTO, GdisCurrent],
):
    model = GdisCurrent

    async def get_max_abai_id(self) -> int:
        result = await self.session.execute(select(func.max(GdisCurrent.abai_id)))
        return result.scalar() or 0

    async def list_existing_abai_ids(self, abai_ids: Sequence[int]) -> set[int]:
        """Какие из исследований уже есть в зеркале (для фильтра значений)."""
        if not abai_ids:
            return set()
        result = await self.session.execute(
            select(GdisCurrent.abai_id).where(GdisCurrent.abai_id.in_(abai_ids)),
        )
        return set(result.scalars().all())

    async def list_by_abai_well_id(
        self,
        abai_well_id: int,
        *,
        limit: int | None = None,
    ) -> Sequence[GdisCurrent]:
        """Исследования скважины, свежие сверху."""
        return await self.get_list(
            QuerySpec(
                filters=(GdisCurrent.abai_well_id == abai_well_id,),
                order_by=(GdisCurrent.meas_date.desc(), GdisCurrent.abai_id.desc()),
                limit=limit,
            ),
        )

    async def list_abai_ids_since(self, since: date) -> list[int]:
        result = await self.session.execute(
            select(GdisCurrent.abai_id)
            .where(GdisCurrent.meas_date >= since)
            .order_by(GdisCurrent.abai_id),
        )
        return list(result.scalars().all())

    async def upsert_many(self, data: Sequence[CreateGdisCurrentDTO]) -> None:
        await _upsert_by_abai_id(self, data)


class GdisCurrentValueRepository(
    AsyncAlchemyRepository[
        CreateGdisCurrentValueDTO,
        UpdateGdisCurrentValueDTO,
        GdisCurrentValue,
    ],
):
    model = GdisCurrentValue

    async def get_max_abai_id(self) -> int:
        result = await self.session.execute(
            select(func.max(GdisCurrentValue.abai_id)),
        )
        return result.scalar() or 0

    async def list_by_gdis_abai_ids(
        self,
        gdis_abai_ids: Sequence[int],
    ) -> Sequence[GdisCurrentValue]:
        if not gdis_abai_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(GdisCurrentValue.gdis_current_abai_id.in_(gdis_abai_ids),),
                order_by=(GdisCurrentValue.abai_id,),
            ),
        )

    async def upsert_many(self, data: Sequence[CreateGdisCurrentValueDTO]) -> None:
        await _upsert_by_abai_id(self, data)

    async def get_last_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
        *,
        metric_abai_ids: Sequence[int],
    ) -> list[tuple[int, float, date]]:
        """Последнее непустое значение метрик по скважине: (abai_well_id,
        value_double, meas_date).

        LATERAL на скважину: исследования читаются по индексу
        ``(abai_well_id, meas_date)`` от свежих к старым, значения — по
        ``(gdis_current_abai_id, metric_abai_id)``; первое найденное и есть
        ответ. Скважины без такого значения в результат не попадают.
        """
        if not abai_well_ids or not metric_abai_ids:
            return []

        requested = select(
            func.unnest(cast(list(abai_well_ids), ARRAY(BigInteger))).label(
                "abai_well_id",
            ),
        ).subquery("requested")
        last_value = (
            select(GdisCurrentValue.value_double, GdisCurrent.meas_date)
            .join(
                GdisCurrent,
                GdisCurrent.abai_id == GdisCurrentValue.gdis_current_abai_id,
            )
            .where(
                GdisCurrent.abai_well_id == requested.c.abai_well_id,
                GdisCurrentValue.metric_abai_id.in_(metric_abai_ids),
                GdisCurrentValue.value_double.is_not(None),
            )
            .order_by(
                GdisCurrent.meas_date.desc(),
                GdisCurrent.abai_id.desc(),
                GdisCurrentValue.abai_id.desc(),
            )
            .limit(1)
            .lateral("last_value")
        )
        stmt = (
            select(
                requested.c.abai_well_id,
                last_value.c.value_double,
                last_value.c.meas_date,
            )
            .select_from(requested)
            .join(last_value, true())
        )
        result = await self.session.execute(stmt)
        return [(row[0], float(row[1]), row[2]) for row in result.all()]
