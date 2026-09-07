from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import BigInteger, RowMapping, cast, func, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
# 1614 «Ток двигателя», 1997 «Относительное заполнение насоса»,
# 1999 «Статус (VLT SALT)» — статус станции: онлайн / не онлайн.
ROTOR_SPEED_REGISTER = 1998
PUMP_MOMENT_REGISTER = 1991
ENGINE_CURRENT_REGISTER = 1614
PUMP_FILL_REGISTER = 1997
VLT_STATUS_REGISTER = 1999

# Сколько последних отсчётов станции просматривать в поисках заполненного
# регистра 1999. Отсчёт идёт раз в ~2 минуты, регистр отсутствует примерно в
# каждой десятой строке случайным образом — 50 строк (~100 минут) покрывают это
# с запасом, а станция, не отдававшая статус дольше, актуального статуса не имеет.
VLT_STATUS_LOOKBACK_ROWS = 50

# Порядок колонок для COPY в telemetry_sdmo_fc_data (id/created_at заполняет БД).
# station_id — локальный telemetry_sdmo_station.id, abai_ngdu_id — НГДУ источника.
FC_DATA_COPY_COLUMNS: tuple[str, ...] = (
    "sdmo_id",
    "station_id",
    "abai_ngdu_id",
    "day",
    "savetime",
    *(f"r_{addr}" for addr in SDMO_REGISTERS),
)

# Атрибуты станции, которые источник может менять между выгрузками.
_STATION_MUTABLE_COLUMNS: tuple[str, ...] = (
    "place_id",
    "name",
    "code",
    "type_1900",
    "serial_number",
    "active",
    "status",
    "well_id",
)
# Описательные поля регистра; ключ (type_1900, addr) и sdmo_id не трогаются.
_FC_REG_MUTABLE_COLUMNS: tuple[str, ...] = (
    "name",
    "units",
    "koef",
    "type",
    "dynamic",
    "info",
    "lora_bytes_size",
)


class SdmoStationRepository(
    AsyncAlchemyRepository[CreateSdmoStationDTO, UpdateSdmoStationDTO, SdmoStation],
):
    model = SdmoStation

    async def get_by_ngdu_sdmo_id(
        self,
        abai_ngdu_id: int,
        sdmo_id: int,
    ) -> SdmoStation | None:
        """Станция по натуральному id — только вместе с НГДУ: у каждого НГДУ
        своя нумерация."""
        return await self.get_one(
            QuerySpec(
                filters=(
                    SdmoStation.abai_ngdu_id == abai_ngdu_id,
                    SdmoStation.sdmo_id == sdmo_id,
                ),
            ),
        )

    async def get_by_code(self, code: str) -> SdmoStation | None:
        return await self.get_one(
            QuerySpec(filters=(SdmoStation.code == code,)),
        )

    async def list_by_well_id(self, well_id: int) -> Sequence[SdmoStation]:
        return await self.get_list(
            QuerySpec(
                filters=(SdmoStation.well_id == well_id,),
                order_by=(SdmoStation.id,),
            ),
        )

    async def list_by_ngdu(
        self,
        abai_ngdu_id: int,
        sdmo_ids: Sequence[int] | None = None,
    ) -> Sequence[SdmoStation]:
        """Станции НГДУ; ``sdmo_ids`` — натуральные id источника для сужения."""
        filters = [SdmoStation.abai_ngdu_id == abai_ngdu_id]
        if sdmo_ids is not None:
            filters.append(SdmoStation.sdmo_id.in_(sdmo_ids))
        return await self.get_list(
            QuerySpec(filters=tuple(filters), order_by=(SdmoStation.sdmo_id,)),
        )

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[SdmoStation]:
        if not ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(SdmoStation.id.in_(ids),), order_by=(SdmoStation.id,)),
        )

    async def upsert_many(self, data: Sequence[CreateSdmoStationDTO]) -> None:
        """Обновить справочник станций, не удаляя строк.

        Ключ — ``(abai_ngdu_id, sdmo_id)``. Удалять/пересоздавать станции нельзя:
        на локальный ``id`` ссылаются fc_data и курсоры/инциденты детекторов.
        Станция, пропавшая из источника, остаётся с последними атрибутами.
        Все изменяемые атрибуты перезаписываются значениями из ``data`` — DTO
        должны быть собраны из полной строки источника. Дыры в ``id`` новых
        станций нормальны: ``ON CONFLICT`` расходует sequence и на конфликтах.
        """
        if not data:
            return
        stmt = pg_insert(SdmoStation).values([item.model_dump() for item in data])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_telemetry_sdmo_station_ngdu_sdmo_id",
            set_={
                **{
                    column: getattr(stmt.excluded, column)
                    for column in _STATION_MUTABLE_COLUMNS
                },
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)


class SdmoFcRegRepository(
    AsyncAlchemyRepository[CreateSdmoFcRegDTO, UpdateSdmoFcRegDTO, SdmoFcReg],
):
    model = SdmoFcReg

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

    async def upsert_many(self, data: Sequence[CreateSdmoFcRegDTO]) -> None:
        """Справочник регистров общий для всех НГДУ, ключ — ``(type_1900, addr)``.

        Словари баз SDMO расходятся по натуральному id, поэтому конфликт
        ловится по паре: описательные поля обновляются, ``sdmo_id`` остаётся
        от первого источника. Дубли пары внутри одного батча схлопываются
        (побеждает последний), иначе ``ON CONFLICT`` отказывается менять
        одну строку дважды.
        """
        if not data:
            return
        by_key = {(item.type_1900, item.addr): item for item in data}
        stmt = pg_insert(SdmoFcReg).values(
            [item.model_dump() for item in by_key.values()],
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_telemetry_sdmo_fc_reg_type_1900_addr",
            set_={
                **{
                    column: getattr(stmt.excluded, column)
                    for column in _FC_REG_MUTABLE_COLUMNS
                },
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)


class SdmoFcDataRepository(
    AsyncAlchemyRepository[RepositoryDTO, RepositoryDTO, SdmoFcData],
):
    model = SdmoFcData

    async def get_last_cursor_by_station(
        self,
        station_ids: Sequence[int],
    ) -> dict[int, tuple[datetime, int]]:
        """Курсор ``(max savetime, sdmo_id при этом savetime)`` на станцию.

        Ключ — локальный ``station_id``. Пара нужна для keyset-пагинации
        источника по индексу ``trend(station_id, savetime, day)``: ``id`` в
        индекс не входит и служит только tiebreak'ом внутри одинакового
        savetime. LATERAL с ``LIMIT 1`` на станцию — один обратный index scan
        по ``(station_id, savetime)``, независимо от глубины истории (DISTINCT
        ON прошёл бы все строки станций). Станции без строк в результат не
        попадают (вызывающий берёт ``(None, 0)`` — «грузим с начала»).
        """
        if not station_ids:
            return {}

        requested = select(
            func.unnest(cast(list(station_ids), ARRAY(BigInteger))).label(
                "station_id",
            ),
        ).subquery("requested")
        last_row = (
            select(SdmoFcData.savetime, SdmoFcData.sdmo_id)
            .where(SdmoFcData.station_id == requested.c.station_id)
            .order_by(SdmoFcData.savetime.desc(), SdmoFcData.sdmo_id.desc())
            .limit(1)
            .lateral("last_row")
        )
        stmt = (
            select(requested.c.station_id, last_row.c.savetime, last_row.c.sdmo_id)
            .select_from(requested)
            .join(last_row, true())
        )
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

    async def list_by_station(self, station_id: int) -> Sequence[SdmoFcData]:
        return await self.get_list(
            QuerySpec(
                filters=(SdmoFcData.station_id == station_id,),
                order_by=(SdmoFcData.savetime,),
            ),
        )

    async def list_by_station_period(
        self,
        station_id: int,
        start: date,
        end: date,
    ) -> Sequence[SdmoFcData]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    SdmoFcData.station_id == station_id,
                    SdmoFcData.savetime >= start,
                    SdmoFcData.savetime <= end,
                ),
                order_by=(SdmoFcData.savetime,),
            ),
        )

    async def get_last_savetime_by_well_ids(
        self,
        well_ids: Sequence[int],
    ) -> dict[int, datetime]:
        """Время последнего отсчёта СДМО на скважину — один запрос на матрицу.

        Идёт от станций (``SdmoStation.well_id``) к данным коррелированным
        ``max(savetime)`` по станции: равенство по ``station_id`` + max по
        второй колонке индекса ``ix_telemetry_sdmo_fc_data_station_savetime``
        — это обратный index scan на одну строку, а не сортировка всех строк
        станции. Скважины без станций или без отсчётов в ответ не попадают;
        у скважины с несколькими станциями берётся самый поздний отсчёт.
        """
        if not well_ids:
            return {}

        last_savetime = (
            select(func.max(SdmoFcData.savetime))
            .where(SdmoFcData.station_id == SdmoStation.id)
            .scalar_subquery()
        )
        stmt = (
            select(SdmoStation.well_id, func.max(last_savetime))
            .where(SdmoStation.well_id.in_(well_ids))
            .group_by(SdmoStation.well_id)
        )
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all() if row[1] is not None}

    async def get_last_vlt_status_by_well_ids(
        self,
        well_ids: Sequence[int],
    ) -> dict[int, int]:
        """Актуальное значение регистра 1999 «Статус (VLT SALT)» на скважину.

        Регистр приходит не в каждом отсчёте (примерно каждая десятая строка
        без него), поэтому берётся последняя заполненная строка среди
        ``VLT_STATUS_LOOKBACK_ROWS`` последних отсчётов станции. Хвост станции
        читается обратным index scan'ом по
        ``ix_telemetry_sdmo_fc_data_station_savetime`` с ``LIMIT`` — ровно
        столько строк на станцию, сколько задано, независимо от глубины
        истории. Фильтр ``IS NOT NULL`` поверх неограниченного скана здесь
        недопустим: у станции, которая регистр не отдаёт вовсе (тип 12) или
        перестала отдавать, он перебирал бы всю её историю — сотни тысяч строк
        на станцию, и матрица НГДУ уходила в таймаут.

        У скважины с несколькими станциями побеждает самое позднее значение.
        Скважины без станций или без заполненного значения в хвосте в ответ
        не попадают.

        Значение отдаётся как есть (в источнике регистр Int32/Uint32):
        1 — станция онлайн, 0 — не онлайн.
        """
        if not well_ids:
            return {}

        vlt_status = getattr(SdmoFcData, f"r_{VLT_STATUS_REGISTER}")
        recent = (
            select(SdmoFcData.savetime, vlt_status.label("vlt_status"))
            .where(SdmoFcData.station_id == SdmoStation.id)
            .order_by(SdmoFcData.savetime.desc())
            .limit(VLT_STATUS_LOOKBACK_ROWS)
            .correlate(SdmoStation)
            .subquery("recent")
        )
        last_status = (
            select(recent.c.savetime, recent.c.vlt_status)
            .where(recent.c.vlt_status.is_not(None))
            .order_by(recent.c.savetime.desc())
            .limit(1)
            .lateral("last_status")
        )
        stmt = (
            select(SdmoStation.well_id, last_status.c.vlt_status)
            .join(last_status, true())
            .where(SdmoStation.well_id.in_(well_ids))
            .distinct(SdmoStation.well_id)
            .order_by(SdmoStation.well_id, last_status.c.savetime.desc())
        )
        result = await self.session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    async def get_last_pump_parameters_by_stations(
        self,
        station_ids: Sequence[int],
    ) -> RowMapping | None:
        """Последний отсчёт (savetime, момент, скорость, заполнение) по станциям.

        ``station_ids`` — локальные id станций. Запрос делается по одной
        станции за раз: равенство по station_id + LIMIT 1 — это обратный
        index scan по ``ix_telemetry_sdmo_fc_data_station_savetime``, тогда как
        фильтр ``IN (...)`` с ``ORDER BY savetime DESC`` заставил бы сортировать
        все строки станций (сотни тысяч на станцию). У скважины обычно одна
        станция.
        """
        rows: list[RowMapping] = []
        for station_id in station_ids:
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
                    filters=(SdmoFcData.station_id == station_id,),
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
        station_ids: Sequence[int],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[RowMapping]:
        """Ряд (savetime, rotor_speed, pump_moment, engine_current) по станциям
        (локальные id)."""
        filters: list = [SdmoFcData.station_id.in_(station_ids)]
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
