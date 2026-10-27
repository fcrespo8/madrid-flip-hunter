"""add market_indices

Valores del IPV del INE (índice de precios de vivienda, base 2025 = 100) por geografía, serie y trimestre.
Sirven para actualizar un precio registral de un año a hoy (rollforward_factor). Se llena con
`python -m scripts.load_ipv`. Solo se insertan filas. Diseño: docs/market_prices_design.md.

Revision ID: b9c4e7a1d3f8
Revises: a8b3d6f1e2c7
Create Date: 2026-10-27

"""
from alembic import op
import sqlalchemy as sa

revision = 'b9c4e7a1d3f8'
down_revision = 'a8b3d6f1e2c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'market_indices',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(30), nullable=False),
        sa.Column('geography', sa.String(60), nullable=False),
        sa.Column('series', sa.String(20), nullable=False),
        sa.Column('period', sa.String(6), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('import_id', sa.Integer(), sa.ForeignKey('market_imports.id'), nullable=False),
        sa.UniqueConstraint('source', 'geography', 'series', 'period', 'import_id', name='uq_market_indices_point_import'),
        sa.CheckConstraint("series IN ('general', 'nueva', 'segunda_mano')", name='ck_market_indices_series'),
        sa.CheckConstraint('value > 0', name='ck_market_indices_value'),
    )
    op.create_index('ix_market_indices_lookup', 'market_indices', ['source', 'geography', 'series', 'period_end'])
    op.create_index('ix_market_indices_import_id', 'market_indices', ['import_id'])


def downgrade() -> None:
    op.drop_index('ix_market_indices_import_id', table_name='market_indices')
    op.drop_index('ix_market_indices_lookup', table_name='market_indices')
    op.drop_table('market_indices')
