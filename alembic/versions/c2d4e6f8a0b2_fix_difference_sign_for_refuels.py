"""fix difference sign for refuel_entries

Revision ID: c2d4e6f8a0b2
Revises: a2b4c6d8e0f2
Create Date: 2026-06-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d4e6f8a0b2'
down_revision: Union[str, Sequence[str], None] = 'a2b4c6d8e0f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE refuel_entries
        SET difference = -difference,
            error_percent = CASE WHEN pilot_amount > 0
                THEN -difference / pilot_amount * 100 ELSE NULL END
        WHERE difference IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE refuel_entries
        SET difference = -difference,
            error_percent = CASE WHEN pilot_amount > 0
                THEN -difference / pilot_amount * 100 ELSE NULL END
        WHERE difference IS NOT NULL
    """)
