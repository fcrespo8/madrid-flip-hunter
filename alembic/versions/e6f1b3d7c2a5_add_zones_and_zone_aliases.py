"""add zones y zone_aliases

Zonas oficiales de Madrid (ciudad, distritos, barrios) y los textos con que los anuncios las
nombran. Se siembran con `python -m scripts.seed_zones` desde data/raw/precio_registral_barrio.csv.
Diseño: docs/market_prices_design.md.

Revision ID: e6f1b3d7c2a5
Revises: d5e9a2c6b1f4
Create Date: 2026-10-23

"""
from alembic import op
import sqlalchemy as sa

revision = 'e6f1b3d7c2a5'
down_revision = 'd5e9a2c6b1f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'zones',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('level', sa.String(10), nullable=False),
        sa.Column('code', sa.String(10), nullable=True),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('name_norm', sa.String(150), nullable=False),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('zones.id'), nullable=True),
        sa.CheckConstraint("level IN ('ciudad', 'distrito', 'barrio')", name='ck_zones_level'),
        sa.UniqueConstraint('level', 'code', name='uq_zones_level_code'),
    )
    op.create_index('ix_zones_name_norm', 'zones', ['name_norm'])
    op.create_index('ix_zones_parent_id', 'zones', ['parent_id'])

    op.create_table(
        'zone_aliases',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('alias_norm', sa.String(150), nullable=False),
        sa.Column('zone_id', sa.Integer(), sa.ForeignKey('zones.id', ondelete='CASCADE'), nullable=True),
        sa.Column('kind', sa.String(15), nullable=False),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.UniqueConstraint('alias_norm', name='uq_zone_aliases_alias_norm'),
        sa.CheckConstraint("kind IN ('ciudad', 'distrito', 'barrio', 'out_of_scope')", name='ck_zone_aliases_kind'),
        sa.CheckConstraint("source IN ('official', 'manual')", name='ck_zone_aliases_source'),
        sa.CheckConstraint("(kind = 'out_of_scope') = (zone_id IS NULL)", name='ck_zone_aliases_zone_kind'),
    )
    op.create_index('ix_zone_aliases_zone_id', 'zone_aliases', ['zone_id'])


def downgrade() -> None:
    op.drop_index('ix_zone_aliases_zone_id', table_name='zone_aliases')
    op.drop_table('zone_aliases')
    op.drop_index('ix_zones_parent_id', table_name='zones')
    op.drop_index('ix_zones_name_norm', table_name='zones')
    op.drop_table('zones')
