"""add food_logs

Revision ID: c8e51f70a2d4
Revises: f3a9c1d24b7e
Create Date: 2026-11-12 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c8e51f70a2d4'
down_revision: str | None = 'f3a9c1d24b7e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('food_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('meal', sa.String(length=20), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=False),
    sa.Column('fatsecret_food_id', sa.String(length=32), nullable=True),
    sa.Column('servings', sa.Float(), nullable=False),
    sa.Column('kcal', sa.Integer(), nullable=False),
    sa.Column('protein_g', sa.Float(), nullable=True),
    sa.Column('carbs_g', sa.Float(), nullable=True),
    sa.Column('fat_g', sa.Float(), nullable=True),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_food_logs_user_id'), 'food_logs', ['user_id'], unique=False)
    op.create_index('ix_food_logs_user_date', 'food_logs', ['user_id', 'date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_food_logs_user_date', table_name='food_logs')
    op.drop_index(op.f('ix_food_logs_user_id'), table_name='food_logs')
    op.drop_table('food_logs')
