"""add precio_piso to expensecategory enum

Revision ID: a1b2c3d4e5f6
Revises: 0553d75b1b42
Create Date: 2026-05-19 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0553d75b1b42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE expensecategory ADD VALUE IF NOT EXISTS 'precio_piso'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
