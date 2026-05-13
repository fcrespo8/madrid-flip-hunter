"""add_performance_indexes

Revision ID: 66191de6ba9e
Revises: 524b41c51f6f
Create Date: 2026-05-13 12:42:21.938207

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66191de6ba9e'
down_revision: Union[str, Sequence[str], None] = '524b41c51f6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_operation_expenses_operation_id", "operation_expenses", ["operation_id"])
    op.create_index("ix_operation_partners_operation_id", "operation_partners", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_operation_expenses_operation_id", table_name="operation_expenses")
    op.drop_index("ix_operation_partners_operation_id", table_name="operation_partners")
