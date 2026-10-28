"""add zone_indicators

Indicadores descriptivos por zona (renta, valor catastral, antigüedad del parque, paro, crecimiento, tamaño del hogar,
envejecimiento, locales cerrados, densidad) del Panel de indicadores de datos.madrid.es. Solo se insertan filas; cada
carga queda en market_imports. Se llena con `python -m scripts.import_panel`.

Revision ID: c0d5f8b2e4a6
Revises: b9c4e7a1d3f8
Create Date: 2026-10-28

"""
from alembic import op
import sqlalchemy as sa

revision = 'c0d5f8b2e4a6'
down_revision = 'b9c4e7a1d3f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'zone_indicators',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('zone_id', sa.Integer(), sa.ForeignKey('zones.id'), nullable=False),
        sa.Column('indicator', sa.String(40), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(30), nullable=False),
        sa.Column('import_id', sa.Integer(), sa.ForeignKey('market_imports.id'), nullable=False),
        sa.UniqueConstraint('zone_id', 'indicator', 'year', 'import_id', name='uq_zone_indicators_zone_indicator_year_import'),
        sa.CheckConstraint('year BETWEEN 1900 AND 2100', name='ck_zone_indicators_year'),
    )
    op.create_index('ix_zone_indicators_zone_indicator_year', 'zone_indicators', ['zone_id', 'indicator', 'year'])
    op.create_index('ix_zone_indicators_import_id', 'zone_indicators', ['import_id'])


def downgrade() -> None:
    op.drop_index('ix_zone_indicators_import_id', table_name='zone_indicators')
    op.drop_index('ix_zone_indicators_zone_indicator_year', table_name='zone_indicators')
    op.drop_table('zone_indicators')
