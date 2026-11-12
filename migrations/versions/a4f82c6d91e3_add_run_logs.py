"""add run_logs

Revision ID: a4f82c6d91e3
Revises: 8f2e64a91c7d
Create Date: 2026-11-12 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'a4f82c6d91e3'
down_revision: str | None = '8f2e64a91c7d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('run_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('distance_km', sa.Float(), nullable=False),
    sa.Column('duration_min', sa.Float(), nullable=False),
    sa.Column('avg_hr_bpm', sa.Integer(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_run_logs_user_id'), 'run_logs', ['user_id'], unique=False)
    op.create_index('ix_run_logs_user_date', 'run_logs', ['user_id', 'date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_run_logs_user_date', table_name='run_logs')
    op.drop_index(op.f('ix_run_logs_user_id'), table_name='run_logs')
    op.drop_table('run_logs')
