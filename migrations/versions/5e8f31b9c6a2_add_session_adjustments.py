"""add session_adjustments and food_logs.fatsecret_entry_id

Revision ID: 5e8f31b9c6a2
Revises: 7a2c94e15d8b
Create Date: 2026-11-12 11:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '5e8f31b9c6a2'
down_revision: str | None = '7a2c94e15d8b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('session_adjustments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'date', name='uq_adjustment_user_date')
    )
    op.create_index(op.f('ix_session_adjustments_user_id'), 'session_adjustments', ['user_id'], unique=False)
    # Lets delete_meal also remove the meal from the user's real FatSecret diary.
    op.add_column('food_logs', sa.Column('fatsecret_entry_id', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('food_logs', 'fatsecret_entry_id')
    op.drop_index(op.f('ix_session_adjustments_user_id'), table_name='session_adjustments')
    op.drop_table('session_adjustments')
