"""Управление вторичными индексами telemetry_sdmo_fc_data для bulk-загрузки.

При заливке истории в ПУСТУЮ таблицу вторичные индексы сносятся, данные
грузятся COPY, а индексы строятся один раз в конце — это в разы быстрее, чем
поддерживать их на каждой вставке. PK (id) не трогаем: он на монотонном
serial, дописывается в правый край B-дерева и почти не мешает.

Индексы общие для всех НГДУ: пока их нет, матрица, карточка и детекторы уже
загруженных НГДУ работают seq scan'ом. Поэтому bulk_load сносит их только по
явному ``--rebuild-indexes``; инкремент их не трогает вовсе.
"""

from sqlalchemy import text

from core import get_logger
from shared.database.sql.setup import engines

logger = get_logger(__name__)

_TABLE = "telemetry_sdmo_fc_data"

# Имя индекса -> DDL создания (идемпотентно). Соответствуют модели SdmoFcData:
# уникальность натурального id внутри станции + композит (station_id, savetime).
SECONDARY_INDEXES: dict[str, str] = {
    "uq_telemetry_sdmo_fc_data_station_sdmo_id": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_telemetry_sdmo_fc_data_station_sdmo_id "
        f"ON {_TABLE} (station_id, sdmo_id)"
    ),
    "ix_telemetry_sdmo_fc_data_station_savetime": (
        "CREATE INDEX IF NOT EXISTS ix_telemetry_sdmo_fc_data_station_savetime "
        f"ON {_TABLE} (station_id, savetime)"
    ),
}


async def drop_secondary_indexes() -> None:
    """Снести вторичные индексы (перед bulk-загрузкой в пустую таблицу)."""
    async with engines["app"].connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name in SECONDARY_INDEXES:
            logger.info("Dropping index %s", name)
            await ac.execute(text(f"DROP INDEX IF EXISTS {name}"))


async def create_secondary_indexes() -> None:
    """Построить вторичные индексы (после bulk-загрузки)."""
    async with engines["app"].connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name, ddl in SECONDARY_INDEXES.items():
            logger.info("Creating index %s", name)
            await ac.execute(text(ddl))


async def analyze_table() -> None:
    """Обновить статистику планировщику после массовой загрузки."""
    async with engines["app"].connect() as conn:
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await ac.execute(text(f"ANALYZE {_TABLE}"))
