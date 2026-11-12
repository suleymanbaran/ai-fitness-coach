"""add meal_templates, workout_templates and food_logs.fiber_g

Revision ID: 8f2e64a91c7d
Revises: 6c4a81d7f2e9
Create Date: 2026-11-12 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '8f2e64a91c7d'
down_revision: str | None = '6c4a81d7f2e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('meal_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('meal', sa.String(length=20), nullable=False),
    sa.Column('items', JSONB(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name', name='uq_meal_template_user_name')
    )
    op.create_index(op.f('ix_meal_templates_user_id'), 'meal_templates', ['user_id'], unique=False)
    op.create_table('workout_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('exercises', JSONB(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name', name='uq_workout_template_user_name')
    )
    op.create_index(op.f('ix_workout_templates_user_id'), 'workout_templates', ['user_id'], unique=False)
    op.add_column('food_logs', sa.Column('fiber_g', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('food_logs', 'fiber_g')
    op.drop_index(op.f('ix_workout_templates_user_id'), table_name='workout_templates')
    op.drop_table('workout_templates')
    op.drop_index(op.f('ix_meal_templates_user_id'), table_name='meal_templates')
    op.drop_table('meal_templates')
