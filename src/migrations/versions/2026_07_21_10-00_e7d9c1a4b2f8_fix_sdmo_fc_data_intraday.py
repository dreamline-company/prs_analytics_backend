"""fix sdmo fc_data for intraday series (drop station/day unique, unique sdmo_id, station/savetime index)

Revision ID: e7d9c1a4b2f8
Revises: da32830be32d
Create Date: 2026-07-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7d9c1a4b2f8'
down_revision: Union[str, Sequence[str], None] = 'da32830be32d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    fc_data_day_parted is an intraday series (~1 row / 2 min per station), not a
    daily aggregate. The old UNIQUE(sdmo_station_id, day) made the copy unable to
    store more than one sample per day. Replace it with a natural-key uniqueness
    on sdmo_id and add a (sdmo_station_id, savetime) index for detector windows.
    """
    op.drop_constraint(
        'uq_telemetry_sdmo_fc_data_station_day',
        'telemetry_sdmo_fc_data',
        type_='unique',
    )
    op.drop_index(
        op.f('ix_telemetry_sdmo_fc_data_sdmo_id'),
        table_name='telemetry_sdmo_fc_data',
    )
    op.create_index(
        op.f('ix_telemetry_sdmo_fc_data_sdmo_id'),
        'telemetry_sdmo_fc_data',
        ['sdmo_id'],
        unique=True,
    )
    op.create_index(
        'ix_telemetry_sdmo_fc_data_station_savetime',
        'telemetry_sdmo_fc_data',
        ['sdmo_station_id', 'savetime'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_telemetry_sdmo_fc_data_station_savetime',
        table_name='telemetry_sdmo_fc_data',
    )
    op.drop_index(
        op.f('ix_telemetry_sdmo_fc_data_sdmo_id'),
        table_name='telemetry_sdmo_fc_data',
    )
    op.create_index(
        op.f('ix_telemetry_sdmo_fc_data_sdmo_id'),
        'telemetry_sdmo_fc_data',
        ['sdmo_id'],
        unique=False,
    )
    op.create_unique_constraint(
        'uq_telemetry_sdmo_fc_data_station_day',
        'telemetry_sdmo_fc_data',
        ['sdmo_station_id', 'day'],
    )
