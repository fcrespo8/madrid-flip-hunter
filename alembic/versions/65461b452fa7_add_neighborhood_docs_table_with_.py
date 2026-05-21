"""add neighborhood_docs table with pgvector

Revision ID: 65461b452fa7
Revises: c3d4e5f6a7b8
Create Date: 2026-05-21 11:34:15.866616

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = '65461b452fa7'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Asegurar que la extensión vector está activada (idempotente)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Crear la tabla
    op.create_table(
        "neighborhood_docs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("barrio", sa.String(length=100), nullable=False),
        sa.Column("distrito", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
    )

    # 3. Índices B-tree para filtrar por barrio/distrito
    op.create_index(
        "ix_neighborhood_docs_barrio",
        "neighborhood_docs",
        ["barrio"],
    )
    op.create_index(
        "ix_neighborhood_docs_distrito",
        "neighborhood_docs",
        ["distrito"],
    )

    # 4. Índice HNSW para búsqueda por similitud coseno
    op.execute(
        "CREATE INDEX ix_neighborhood_docs_embedding_hnsw "
        "ON neighborhood_docs "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_neighborhood_docs_embedding_hnsw")
    op.drop_index("ix_neighborhood_docs_distrito", table_name="neighborhood_docs")
    op.drop_index("ix_neighborhood_docs_barrio", table_name="neighborhood_docs")
    op.drop_table("neighborhood_docs")
    # Nota: NO bajamos la extensión vector porque otras tablas podrían usarla
