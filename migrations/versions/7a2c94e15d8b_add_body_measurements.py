"""add body_measurements

Revision ID: 7a2c94e15d8b
Revises: 9d4b2e88c1f5
Create Date: 2026-11-12 09:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '7a2c94e15d8b'
down_revision: str | None = '9d4b2e88c1f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('body_measurements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('weight_kg', sa.Float(), nullable=True),
    sa.Column('body_fat_pct', sa.Float(), nullable=True),
    sa.Column('waist_cm', sa.Float(), nullable=True),
    sa.Column('chest_cm', sa.Float(), nullable=True),
    sa.Column('arm_cm', sa.Float(), nullable=True),
    sa.Column('thigh_cm', sa.Float(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'date', name='uq_measurements_user_date')
    )
    op.create_index(op.f('ix_body_measurements_user_id'), 'body_measurements', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_body_measurements_user_id'), table_name='body_measurements')
    op.drop_table('body_measurements')
