"""add rod break detector tables

Revision ID: a3b6c9d2e5f8
Revises: e7d9c1a4b2f8
Create Date: 2026-07-21 10:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3b6c9d2e5f8'
down_revision: Union[str, Sequence[str], None] = 'e7d9c1a4b2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'detector_rod_break_run',
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('wells_scanned', sa.Integer(), nullable=False),
        sa.Column('detections_count', sa.Integer(), nullable=False),
        sa.Column('config_version', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_detector_rod_break_run')),
    )
    op.create_index(op.f('ix_detector_rod_break_run_as_of_date'), 'detector_rod_break_run', ['as_of_date'], unique=False)
    op.create_index(op.f('ix_detector_rod_break_run_id'), 'detector_rod_break_run', ['id'], unique=False)

    op.create_table(
        'detector_rod_break_detection',
        sa.Column('run_id', sa.BigInteger(), nullable=False),
        sa.Column('well_id', sa.BigInteger(), nullable=False),
        sa.Column('station_sdmo_id', sa.Integer(), nullable=True),
        sa.Column('detector_code', sa.String(length=10), nullable=False),
        sa.Column('fired', sa.Boolean(), nullable=False),
        sa.Column('fired_at', sa.DateTime(), nullable=True),
        sa.Column('failure_dt', sa.DateTime(), nullable=True),
        sa.Column('lead_time_hours', sa.Float(), nullable=True),
        sa.Column('event_class', sa.String(length=30), nullable=True),
        sa.Column('base_moment', sa.Float(), nullable=True),
        sa.Column('low_confidence', sa.Boolean(), nullable=False),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['run_id'],
            ['detector_rod_break_run.id'],
            name=op.f('fk_detector_rod_break_detection_run_id_detector_rod_break_run'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['well_id'],
            ['wells_well.id'],
            name=op.f('fk_detector_rod_break_detection_well_id_wells_well'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_detector_rod_break_detection')),
    )
    op.create_index(op.f('ix_detector_rod_break_detection_id'), 'detector_rod_break_detection', ['id'], unique=False)
    op.create_index(op.f('ix_detector_rod_break_detection_run_id'), 'detector_rod_break_detection', ['run_id'], unique=False)
    op.create_index(op.f('ix_detector_rod_break_detection_well_id'), 'detector_rod_break_detection', ['well_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_detector_rod_break_detection_well_id'), table_name='detector_rod_break_detection')
    op.drop_index(op.f('ix_detector_rod_break_detection_run_id'), table_name='detector_rod_break_detection')
    op.drop_index(op.f('ix_detector_rod_break_detection_id'), table_name='detector_rod_break_detection')
    op.drop_table('detector_rod_break_detection')
    op.drop_index(op.f('ix_detector_rod_break_run_id'), table_name='detector_rod_break_run')
    op.drop_index(op.f('ix_detector_rod_break_run_as_of_date'), table_name='detector_rod_break_run')
    op.drop_table('detector_rod_break_run')
