"""sdmo multi ngdu

Станции и телеметрия СДМО из нескольких НГДУ (у каждого своя база SDMO с
независимой нумерацией):

* ``telemetry_sdmo_station``: ``abai_ngdu_id`` (НГДУ-источник), ключ станции
  внутри НГДУ — ``(abai_ngdu_id, sdmo_id)``. Локальный ``id`` становится
  ключом станции для fc_data и детекторов; существующим станциям (Кайнар,
  KMG = 12) присваивается ``id = sdmo_id``, потому что ``fc_data.sdmo_station_id``
  и ``detectors_cursor/incident.entity_id`` хранят именно натуральный id — так
  все существующие ссылки становятся ссылками на локальный id без перезаписи
  большой таблицы.
* ``telemetry_sdmo_fc_data``: ``sdmo_station_id`` -> ``station_id`` (локальный
  id станции), ``abai_ngdu_id``; уникальность натурального ``sdmo_id`` — только
  внутри станции.

Уникальный индекс fc_data строится CONCURRENTLY вне транзакции: на проде
таблица большая, а инкрементальный загрузчик не должен ждать.

Revision ID: 431d6233b404
Revises: c140d962d55f
Create Date: 2026-09-07 15:08:41.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "431d6233b404"
down_revision: str | Sequence[str] | None = "c140d962d55f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Все существующие данные СДМО — Кайнармунайгаз (AbaiNGDUIDsEnum.KMG).
KMG_ABAI_NGDU_ID = 12


def upgrade() -> None:
    """Upgrade schema."""
    # --- станции: НГДУ-источник и ключ (abai_ngdu_id, sdmo_id) ---
    op.add_column(
        "telemetry_sdmo_station",
        sa.Column(
            "abai_ngdu_id",
            sa.SmallInteger(),
            nullable=False,
            server_default=str(KMG_ABAI_NGDU_ID),
        ),
    )
    op.alter_column("telemetry_sdmo_station", "abai_ngdu_id", server_default=None)
    op.drop_index(
        op.f("ix_telemetry_sdmo_station_sdmo_id"),
        table_name="telemetry_sdmo_station",
    )
    op.create_unique_constraint(
        "uq_telemetry_sdmo_station_ngdu_sdmo_id",
        "telemetry_sdmo_station",
        ["abai_ngdu_id", "sdmo_id"],
    )
    # Локальный id = натуральный id для уже загруженных станций. В два шага
    # через отрицательные значения: прямой UPDATE id = sdmo_id ловит
    # транзитный дубль PK внутри одного оператора.
    op.execute("UPDATE telemetry_sdmo_station SET id = -sdmo_id")
    op.execute("UPDATE telemetry_sdmo_station SET id = -id")
    op.execute(
        "SELECT setval(pg_get_serial_sequence('telemetry_sdmo_station', 'id'), "
        "GREATEST((SELECT max(id) FROM telemetry_sdmo_station), 1))",
    )

    # --- fc_data: локальный ключ станции, НГДУ, уникальность внутри станции ---
    op.alter_column(
        "telemetry_sdmo_fc_data",
        "sdmo_station_id",
        new_column_name="station_id",
    )
    op.add_column(
        "telemetry_sdmo_fc_data",
        sa.Column(
            "abai_ngdu_id",
            sa.SmallInteger(),
            nullable=False,
            server_default=str(KMG_ABAI_NGDU_ID),
        ),
    )
    op.alter_column("telemetry_sdmo_fc_data", "abai_ngdu_id", server_default=None)
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_telemetry_sdmo_fc_data_station_sdmo_id "
            "ON telemetry_sdmo_fc_data (station_id, sdmo_id)",
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_telemetry_sdmo_fc_data_sdmo_id",
        )


def downgrade() -> None:
    """Downgrade schema.

    Возможен только пока загружен один НГДУ: глобальная уникальность sdmo_id
    в fc_data при нескольких источниках не выполняется. Локальные id станций
    остаются равными sdmo_id — старому коду это безразлично.
    """
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_telemetry_sdmo_fc_data_sdmo_id ON telemetry_sdmo_fc_data (sdmo_id)",
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "uq_telemetry_sdmo_fc_data_station_sdmo_id",
        )
    op.drop_column("telemetry_sdmo_fc_data", "abai_ngdu_id")
    op.alter_column(
        "telemetry_sdmo_fc_data",
        "station_id",
        new_column_name="sdmo_station_id",
    )

    op.drop_constraint(
        "uq_telemetry_sdmo_station_ngdu_sdmo_id",
        "telemetry_sdmo_station",
        type_="unique",
    )
    op.create_index(
        op.f("ix_telemetry_sdmo_station_sdmo_id"),
        "telemetry_sdmo_station",
        ["sdmo_id"],
        unique=True,
    )
    op.drop_column("telemetry_sdmo_station", "abai_ngdu_id")
