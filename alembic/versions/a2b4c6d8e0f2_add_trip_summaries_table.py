"""add trip_summaries table

Revision ID: a2b4c6d8e0f2
Revises: ec57a2531e4b
Create Date: 2026-06-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b4c6d8e0f2'
down_revision: Union[str, Sequence[str], None] = 'ec57a2531e4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('trip_summaries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=False),
        sa.Column('motion_seconds', sa.Integer(), nullable=False),
        sa.Column('gps_km', sa.Float(), nullable=False),
        sa.Column('can_km', sa.Float(), nullable=False),
        sa.Column('max_speed', sa.Float(), nullable=False),
        sa.Column('avg_speed', sa.Float(), nullable=False),
        sa.Column('parking_count', sa.Integer(), nullable=False),
        sa.Column('segment_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_trip_summaries_vehicle_id'), 'trip_summaries', ['vehicle_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_trip_summaries_vehicle_id'), table_name='trip_summaries')
    op.drop_table('trip_summaries')
