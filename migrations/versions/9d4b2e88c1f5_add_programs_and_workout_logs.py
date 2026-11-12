"""add programs and workout_logs

Revision ID: 9d4b2e88c1f5
Revises: c8e51f70a2d4
Create Date: 2026-11-12 02:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '9d4b2e88c1f5'
down_revision: str | None = 'c8e51f70a2d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('programs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('summary', sa.String(length=500), nullable=False),
    sa.Column('payload', JSONB(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_programs_user_id'), 'programs', ['user_id'], unique=False)
    op.create_index(
        'uq_programs_one_active', 'programs', ['user_id', 'kind'],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table('workout_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=True),
    sa.Column('session_name', sa.String(length=120), nullable=False),
    sa.Column('exercises', JSONB(), nullable=False),
    sa.Column('duration_min', sa.Integer(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workout_logs_user_id'), 'workout_logs', ['user_id'], unique=False)
    op.create_index('ix_workout_logs_user_date', 'workout_logs', ['user_id', 'date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_workout_logs_user_date', table_name='workout_logs')
    op.drop_index(op.f('ix_workout_logs_user_id'), table_name='workout_logs')
    op.drop_table('workout_logs')
    op.drop_index('uq_programs_one_active', table_name='programs')
    op.drop_index(op.f('ix_programs_user_id'), table_name='programs')
    op.drop_table('programs')
