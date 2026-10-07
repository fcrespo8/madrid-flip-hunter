"""add scrape_runs

Un registro por barrido de cada fuente: cuántos listings vio, cuántos eran nuevos,
si el barrido fue completo (y si no, por qué) y el error si lo hubo.

Revision ID: c4d8f1a7e3b2
Revises: b7e2d9a4c1f0
Create Date: 2026-10-07

"""
from alembic import op
import sqlalchemy as sa

revision = 'c4d8f1a7e3b2'
down_revision = 'b7e2d9a4c1f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scrape_runs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('seen_count', sa.Integer(), nullable=False),
        sa.Column('new_count', sa.Integer(), nullable=False),
        sa.Column('complete', sa.Boolean(), nullable=False),
        sa.Column('total_reported', sa.Integer(), nullable=True),
        sa.Column('incomplete_reason', sa.String(30), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
    )
    op.create_index('ix_scrape_runs_source_created_at', 'scrape_runs', ['source', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_scrape_runs_source_created_at', table_name='scrape_runs')
    op.drop_table('scrape_runs')
