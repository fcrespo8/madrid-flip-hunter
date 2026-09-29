"""add score_status, score_attempts and qa_rejected to listings

QA pasa a marcar (qa_rejected + qa_reason) en vez de borrar, y el ciclo de vida
del score queda explícito en score_status: pending / auto / llm / unscorable / failed.

Revision ID: b7e2d9a4c1f0
Revises: f3a1b2c4d5e6
Create Date: 2026-09-29

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e2d9a4c1f0'
down_revision = 'f3a1b2c4d5e6'
branch_labels = None
depends_on = None

AUTO_REASONING = 'Score automático: precio vs mercado'


def upgrade() -> None:
    op.add_column('listings', sa.Column('qa_rejected', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('listings', sa.Column('qa_reason', sa.Text(), nullable=True))
    op.add_column('listings', sa.Column('score_status', sa.String(20), server_default='pending', nullable=False))
    op.add_column('listings', sa.Column('score_status_reason', sa.Text(), nullable=True))
    op.add_column('listings', sa.Column('score_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False))

    # Backfill. Los que hoy tienen score NULL (incluidos los "no puntuables" del
    # pre_score viejo) quedan en 'pending': la próxima corrida los re-evalúa y
    # guarda el motivo real.
    op.execute(
        sa.text(
            "UPDATE listings SET score_status = 'auto' "
            "WHERE score IS NOT NULL AND score_reasoning = :auto"
        ).bindparams(auto=AUTO_REASONING)
    )
    op.execute("UPDATE listings SET score_status = 'llm' WHERE score IS NOT NULL AND score_status = 'pending'")

    op.create_check_constraint(
        'ck_listings_score_status',
        'listings',
        "score_status IN ('pending', 'auto', 'llm', 'unscorable', 'failed')",
    )
    op.create_index(
        'ix_listings_pending',
        'listings',
        ['id'],
        postgresql_where=sa.text("score_status = 'pending' AND NOT qa_rejected AND is_active"),
    )


def downgrade() -> None:
    op.drop_index('ix_listings_pending', table_name='listings')
    op.drop_constraint('ck_listings_score_status', 'listings', type_='check')
    op.drop_column('listings', 'score_attempts')
    op.drop_column('listings', 'score_status_reason')
    op.drop_column('listings', 'score_status')
    op.drop_column('listings', 'qa_reason')
    op.drop_column('listings', 'qa_rejected')
