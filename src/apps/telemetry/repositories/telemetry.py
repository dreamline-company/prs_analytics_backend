from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import BigInteger, cast, func, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import aliased

from apps.telemetry.dto.internal.repositories.telemetry import (
    CreateTelemetryDTO,
    UpdateTelemetryDTO,
)
from apps.telemetry.models.telemetry import Telemetry
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class TelemetryRepository(
    AsyncAlchemyRepository[CreateTelemetryDTO, UpdateTelemetryDTO, Telemetry],
):
    model = Telemetry

    async def get_by_abai_id(self, abai_id: int) -> Telemetry | None:
        return await self.get_one(
            QuerySpec(
                filters=(Telemetry.abai_id == abai_id,),
            ),
        )

    async def list_by_well_id(self, well_id: int) -> Sequence[Telemetry]:
        return await self.get_list(
            QuerySpec(
                filters=(Telemetry.well_id == well_id,),
                order_by=(Telemetry.date_time,),
            ),
        )

    async def list_by_well_id_in_period(
        self,
        well_id: int,
        *,
        date_time_from: datetime | None = None,
        date_time_to: datetime | None = None,
    ) -> Sequence[Telemetry]:
        filters = [Telemetry.well_id == well_id]
        if date_time_from is not None:
            filters.append(Telemetry.date_time >= date_time_from)
        if date_time_to is not None:
            filters.append(Telemetry.date_time <= date_time_to)

        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(Telemetry.date_time.asc(),),
            ),
        )

    async def update_by_id(
        self,
        telemetry_id: int,
        data: UpdateTelemetryDTO,
    ) -> Telemetry:
        return await self.update(
            data=data,
            filters=(Telemetry.id == telemetry_id,),
        )

    async def delete_by_id(self, telemetry_id: int) -> None:
        await self.delete(filters=(Telemetry.id == telemetry_id,))

    async def get_last_by_well_ids(
        self,
        well_ids: Sequence[int],
    ) -> dict[int, Telemetry]:
        """Последний отсчёт каждой скважины — один запрос на матрицу НГДУ.

        LATERAL с ``LIMIT 1`` на скважину: по индексу (well_id, date_time)
        каждая скважина стоит один обратный index scan, и время не зависит от
        глубины истории. DISTINCT ON по тому же индексу вычитывал бы все
        строки запрошенных скважин (сотни тысяч на НГДУ) и сортировал их.
        ``id`` в сортировке — tiebreak на случай двух записей с одинаковым
        ``date_time``.
        """
        if not well_ids:
            return {}

        requested = select(
            func.unnest(cast(list(well_ids), ARRAY(BigInteger))).label("well_id"),
        ).subquery("requested")
        last_row = (
            select(Telemetry)
            .where(Telemetry.well_id == requested.c.well_id)
            .order_by(Telemetry.date_time.desc(), Telemetry.id.desc())
            .limit(1)
            .lateral("last_row")
        )
        last = aliased(Telemetry, last_row)
        stmt = select(last).select_from(requested).join(last, true())
        result = await self.session.execute(stmt)
        return {row.well_id: row for row in result.scalars()}

    async def get_last_by_well_id(self, well_id: int) -> Telemetry | None:
        tms = await self.get_list(
            QuerySpec(
                filters=(Telemetry.well_id == well_id,),
                order_by=(Telemetry.date_time.desc(),),
                limit=1,
            ),
        )
        return tms[0] if tms else None

    async def get_last_by_ngdu_id(self, abai_ngdu_id: int) -> Telemetry | None:
        tms = await self.get_list(
            QuerySpec(
                filters=(Telemetry.abai_ngdu_id == abai_ngdu_id,),
                order_by=(Telemetry.date_time.desc(),),
                limit=1,
            ),
        )
        return tms[0] if tms else None

    async def get_last_by_well_ids_before(
        self,
        well_ids: Sequence[int],
        *,
        before: datetime,
    ) -> dict[int, Telemetry]:
        """Последний отсчёт каждой скважины строго до ``before``.

        Вариант ``get_last_by_well_ids`` для отчётов «на дату»: тот же LATERAL
        по индексу (well_id, date_time), только с верхней границей времени.
        """
        if not well_ids:
            return {}

        requested = select(
            func.unnest(cast(list(well_ids), ARRAY(BigInteger))).label("well_id"),
        ).subquery("requested")
        last_row = (
            select(Telemetry)
            .where(
                Telemetry.well_id == requested.c.well_id,
                Telemetry.date_time < before,
            )
            .order_by(Telemetry.date_time.desc(), Telemetry.id.desc())
            .limit(1)
            .lateral("last_row")
        )
        last = aliased(Telemetry, last_row)
        stmt = select(last).select_from(requested).join(last, true())
        result = await self.session.execute(stmt)
        return {row.well_id: row for row in result.scalars()}

    async def list_by_well_ids_in_period(
        self,
        well_ids: Sequence[int],
        *,
        date_time_from: datetime,
        date_time_to: datetime,
    ) -> dict[int, list[Telemetry]]:
        """История замеров скважин за окно, по возрастанию времени; ключ — well_id."""
        if not well_ids:
            return {}
        rows = await self.get_list(
            QuerySpec(
                filters=(
                    Telemetry.well_id.in_(well_ids),
                    Telemetry.date_time >= date_time_from,
                    Telemetry.date_time < date_time_to,
                ),
                order_by=(Telemetry.date_time.asc(), Telemetry.id.asc()),
            ),
        )
        history: dict[int, list[Telemetry]] = {}
        for row in rows:
            history.setdefault(row.well_id, []).append(row)
        return history
