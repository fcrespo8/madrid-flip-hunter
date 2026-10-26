"""add market_imports y market_prices

Cargas de precios de mercado (por ahora el registral por barrio) y los precios por zona y período.
Solo se insertan filas: una carga nueva agrega, no pisa. Se llenan con `python -m scripts.import_registral`.
Diseño: docs/market_prices_design.md.

Revision ID: a8b3d6f1e2c7
Revises: f7a2c4e8d9b3
Create Date: 2026-10-26

"""
from alembic import op
import sqlalchemy as sa

revision = 'a8b3d6f1e2c7'
down_revision = 'f7a2c4e8d9b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'market_imports',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(30), nullable=False),
        sa.Column('file_name', sa.String(200), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('period_label', sa.String(30), nullable=False),
        sa.Column('loaded_at', sa.DateTime(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('skipped_count', sa.Integer(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('source', 'sha256', name='uq_market_imports_source_sha256'),
        sa.CheckConstraint('length(sha256) = 64', name='ck_market_imports_sha256'),
    )
    op.create_table(
        'market_prices',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('zone_id', sa.Integer(), sa.ForeignKey('zones.id'), nullable=False),
        sa.Column('source', sa.String(30), nullable=False),
        sa.Column('period', sa.Date(), nullable=False),
        sa.Column('price_m2', sa.Float(), nullable=False),
        sa.Column('import_id', sa.Integer(), sa.ForeignKey('market_imports.id'), nullable=False),
        sa.UniqueConstraint('zone_id', 'source', 'period', 'import_id', name='uq_market_prices_zone_source_period_import'),
        sa.CheckConstraint('price_m2 > 0', name='ck_market_prices_price'),
    )
    op.create_index('ix_market_prices_zone_source_period', 'market_prices', ['zone_id', 'source', 'period'])
    op.create_index('ix_market_prices_import_id', 'market_prices', ['import_id'])


def downgrade() -> None:
    op.drop_index('ix_market_prices_import_id', table_name='market_prices')
    op.drop_index('ix_market_prices_zone_source_period', table_name='market_prices')
    op.drop_table('market_prices')
    op.drop_table('market_imports')
