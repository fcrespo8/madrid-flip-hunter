"""una sola zona de nivel ciudad

`zones.code` es NULL para la ciudad y UNIQUE (level, code) no detecta duplicados con NULL: un segundo
registro de la ciudad pasaba. El índice parcial lo impide.

Revision ID: f7a2c4e8d9b3
Revises: e6f1b3d7c2a5
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7a2c4e8d9b3'
down_revision = 'e6f1b3d7c2a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('uq_zones_single_ciudad', 'zones', ['level'], unique=True,
                    postgresql_where=sa.text("level = 'ciudad'"))


def downgrade() -> None:
    op.drop_index('uq_zones_single_ciudad', table_name='zones')
