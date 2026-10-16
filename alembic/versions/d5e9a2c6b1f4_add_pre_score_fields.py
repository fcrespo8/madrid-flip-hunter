"""add pre_score, discount_pct y market_zone a listings

Lo que calcula el pre-scorer en cada corrida: la nota previa (1/3/5/7/8/9), el % de descuento
sobre el €/m² de mercado y la zona de la tabla de precios con la que se comparó. Son columnas
nuevas y nulas: los listings existentes quedan en NULL hasta que pasen por el pre-scorer o se
corra `python -m scripts.backfill_pre_scores`.

Revision ID: d5e9a2c6b1f4
Revises: c4d8f1a7e3b2
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'd5e9a2c6b1f4'
down_revision = 'c4d8f1a7e3b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('pre_score', sa.Float(), nullable=True))
    op.add_column('listings', sa.Column('discount_pct', sa.Float(), nullable=True))
    op.add_column('listings', sa.Column('market_zone', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'market_zone')
    op.drop_column('listings', 'discount_pct')
    op.drop_column('listings', 'pre_score')
