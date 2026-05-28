"""add_is_active_to_users

Revision ID: a5f49c42ab1a
Revises: 31d3bd7c0777
Create Date: 2026-05-28 21:57:49.521275

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5f49c42ab1a'
down_revision: Union[str, Sequence[str], None] = '31d3bd7c0777'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.alter_column("users", "is_active", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "is_active")
