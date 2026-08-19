from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import RowMapping, delete, select

from apps.telemetry.dto.internal.repositories.sdmo import (
    CreateSdmoFcRegDTO,
    CreateSdmoStationDTO,
    UpdateSdmoFcRegDTO,
    UpdateSdmoStationDTO,
)
from apps.telemetry.models.sdmo import (
    SDMO_REGISTERS,
    SdmoFcData,
    SdmoFcReg,
    SdmoStation,
)
from shared.dto.repositories import RepositoryDTO
from shared.repository.sqlalchemy import (
    AsyncAlchemyRepository,
    ProjectionQuerySpec,
    QuerySpec,
)

# Регистры параметров СДМО для API (type_1900 = 1/6/16; имена из
# telemetry_sdmo_fc_reg): 1998 «Скорость ротора», 1991 «Момент штанги/на валу»,
# 1614 «Ток двигателя», 1997 «Относительное заполнение насоса».
ROTOR_SPEED_REGISTER = 1998
PUMP_MOMENT_REGISTER = 1991
ENGINE_CURRENT_REGISTER = 1614
PUMP_FILL_REGISTER = 1997

# Порядок колонок для COPY в telemetry_sdmo_fc_data (id/created_at заполняет БД).
FC_DATA_COPY_COLUMNS: tuple[str, ...] = (
    "sdmo_id",
    "sdmo_station_id",
    "day",
    "savetime",
    *(f"r_{addr}" for addr in SDMO_REGISTERS),
)


class SdmoStationRepository(
    AsyncAlchemyRepository[CreateSdmoStationDTO, UpdateSdmoStationDTO, SdmoStation],
):
    model = SdmoStation

    async def delete_all(self) -> None:
        await self.session.execute(delete(SdmoStation))

    async def get_by_sdmo_id(self, sdmo_id: int) -> SdmoStation | None:
        return await self.get_one(
            QuerySpec(filters=(SdmoStation.sdmo_id == sdmo_id,)),
        )

    async def get_by_code(self, code: str) -> SdmoStation | None:
        return await self.get_one(
            QuerySpec(filters=(SdmoStation.code == code,)),
        )

    async def list_by_well_id(self, well_id: int) -> Sequence[SdmoStation]:
        return await self.get_list(
            QuerySpec(
                filters=(SdmoStation.well_id == well_id,),
                order_by=(SdmoStation.sdmo_id,),
            ),
        )


class SdmoFcRegRepository(
    AsyncAlchemyRepository[CreateSdmoFcRegDTO, UpdateSdmoFcRegDTO, SdmoFcReg],
):
    model = SdmoFcReg

    async def delete_all(self) -> None:
        await self.session.execute(delete(SdmoFcReg))

    async def get_by_addr(
        self,
        addr: int,
        type_1900: int | None = None,
    ) -> SdmoFcReg | None:
        filters = [SdmoFcReg.addr == addr]
        if type_1900 is not None:
            filters.append(SdmoFcReg.type_1900 == type_1900)

        return await self.get_one(QuerySpec(filters=tuple(filters)))

    async def list_all(self) -> Sequence[SdmoFcReg]:
        return await self.get_list(
            QuerySpec(order_by=(SdmoFcReg.type_1900, SdmoFcReg.addr)),
        )


class SdmoFcDataRepository(
    AsyncAlchemyRepository[RepositoryDTO, RepositoryDTO, SdmoFcData],
):
    model = SdmoFcData

    async def get_last_sdmo_id(self) -> int:
        rows = await self.get_list(
            QuerySpec(
                order_by=(SdmoFcData.sdmo_id.desc(),),
                limit=1,
            ),
        )
        return rows[0].sdmo_id if rows else 0

    async def get_last_cursor_by_station(
        self,
        station_ids: Sequence[int] | None = None,
    ) -> dict[int, tuple[datetime, int]]:
        """Курсор ``(max savetime, sdmo_id при этом savetime)`` на станцию.

        Пара нужна для keyset-пагинации источника по индексу
        ``trend(station_id, savetime, day)``: ``id`` в индекс не входит и
        служит только tiebreak'ом внутри одинакового savetime. Станции без
        строк в результат не попадают (вызывающий берёт ``(None, 0)`` по
        умолчанию — «грузим с начала»).
        """
        stmt = (
            select(
                SdmoFcData.sdmo_station_id,
                SdmoFcData.savetime,
                SdmoFcData.sdmo_id,
            )
            .distinct(SdmoFcData.sdmo_station_id)
            .order_by(
                SdmoFcData.sdmo_station_id,
                SdmoFcData.savetime.desc(),
                SdmoFcData.sdmo_id.desc(),
            )
        )
        if station_ids:
            stmt = stmt.where(SdmoFcData.sdmo_station_id.in_(station_ids))
        result = await self.session.execute(stmt)
        return {row[0]: (row[1], row[2]) for row in result.all()}

    async def copy_rows(self, records: Sequence[tuple]) -> None:
        """Массовая вставка через asyncpg COPY (быстрее executemany в разы).

        ``records`` — кортежи в порядке ``FC_DATA_COPY_COLUMNS``. COPY идёт по
        соединению текущей сессии (в её транзакции); коммитит вызывающий.
        """
        if not records:
            return
        conn = await self.session.connection()
        raw = await conn.get_raw_connection()
        asyncpg_conn = raw.driver_connection
        await asyncpg_conn.copy_records_to_table(
            SdmoFcData.__tablename__,
            records=records,
            columns=FC_DATA_COPY_COLUMNS,
            schema_name="public",
        )

    async def list_by_station(
        self,
        sdmo_station_id: int,
    ) -> Sequence[SdmoFcData]:
        return await self.get_list(
            QuerySpec(
                filters=(SdmoFcData.sdmo_station_id == sdmo_station_id,),
                order_by=(SdmoFcData.savetime,),
            ),
        )

    async def list_by_station_period(
        self,
        sdmo_station_id: int,
        start: date,
        end: date,
    ) -> Sequence[SdmoFcData]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    SdmoFcData.sdmo_station_id == sdmo_station_id,
                    SdmoFcData.savetime >= start,
                    SdmoFcData.savetime <= end,
                ),
                order_by=(SdmoFcData.savetime,),
            ),
        )

    async def get_last_pump_parameters_by_stations(
        self,
        station_sdmo_ids: Sequence[int],
    ) -> RowMapping | None:
        """Последний отсчёт (savetime, момент, скорость, заполнение) по станциям.

        Запрос делается по одной станции за раз: равенство по
        sdmo_station_id + LIMIT 1 — это обратный index scan по
        ``ix_telemetry_sdmo_fc_data_station_savetime``, тогда как фильтр
        ``IN (...)`` с ``ORDER BY savetime DESC`` заставил бы сортировать все
        строки станций (сотни тысяч на станцию). У скважины обычно одна станция.
        """
        rows: list[RowMapping] = []
        for station_sdmo_id in station_sdmo_ids:
            found = await self.get_projection_list(
                ProjectionQuerySpec(
                    joins=(),
                    fields=(
                        SdmoFcData.savetime,
                        getattr(SdmoFcData, f"r_{PUMP_MOMENT_REGISTER}").label(
                            "pump_moment",
                        ),
                        getattr(SdmoFcData, f"r_{ROTOR_SPEED_REGISTER}").label(
                            "pump_speed",
                        ),
                        getattr(SdmoFcData, f"r_{PUMP_FILL_REGISTER}").label(
                            "pump_fill",
                        ),
                    ),
                    filters=(SdmoFcData.sdmo_station_id == station_sdmo_id,),
                    order_by=(SdmoFcData.savetime.desc(),),
                    limit=1,
                ),
            )
            rows.extend(found)

        if not rows:
            return None

        return max(rows, key=lambda row: row["savetime"])

    async def list_parameters_by_stations_period(
        self,
        station_sdmo_ids: Sequence[int],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[RowMapping]:
        """Ряд (savetime, rotor_speed, pump_moment, engine_current) по станциям."""
        filters: list = [SdmoFcData.sdmo_station_id.in_(station_sdmo_ids)]
        if start_time is not None:
            filters.append(SdmoFcData.savetime >= start_time)
        if end_time is not None:
            filters.append(SdmoFcData.savetime <= end_time)

        return await self.get_projection_list(
            ProjectionQuerySpec(
                joins=(),
                fields=(
                    SdmoFcData.savetime,
                    getattr(SdmoFcData, f"r_{ROTOR_SPEED_REGISTER}").label(
                        "rotor_speed",
                    ),
                    getattr(SdmoFcData, f"r_{PUMP_MOMENT_REGISTER}").label(
                        "pump_moment",
                    ),
                    getattr(SdmoFcData, f"r_{ENGINE_CURRENT_REGISTER}").label(
                        "engine_current",
                    ),
                ),
                filters=tuple(filters),
                order_by=(SdmoFcData.savetime,),
            ),
        )
