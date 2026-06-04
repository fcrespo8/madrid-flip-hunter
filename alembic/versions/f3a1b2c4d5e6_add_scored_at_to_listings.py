"""add scored_at to listings

Revision ID: f3a1b2c4d5e6
Revises: 159e9c154a2f
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'f3a1b2c4d5e6'
down_revision = '159e9c154a2f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('scored_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'scored_at')
