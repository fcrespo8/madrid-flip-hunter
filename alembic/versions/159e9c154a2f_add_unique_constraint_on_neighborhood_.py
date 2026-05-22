"""add unique constraint on neighborhood_docs barrio distrito

Revision ID: 159e9c154a2f
Revises: 65461b452fa7
Create Date: 2026-05-22 11:48:15.617174

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '159e9c154a2f'
down_revision: Union[str, Sequence[str], None] = '65461b452fa7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_neighborhood_docs_barrio_distrito",
        "neighborhood_docs",
        ["barrio", "distrito"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_neighborhood_docs_barrio_distrito",
        "neighborhood_docs",
        type_="unique",
    )
