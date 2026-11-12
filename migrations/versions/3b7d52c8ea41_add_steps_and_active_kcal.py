"""add steps and active_kcal to daily_biometrics

Revision ID: 3b7d52c8ea41
Revises: 5e8f31b9c6a2
Create Date: 2026-11-12 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '3b7d52c8ea41'
down_revision: str | None = '5e8f31b9c6a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('daily_biometrics', sa.Column('steps', sa.Integer(), nullable=True))
    op.add_column('daily_biometrics', sa.Column('active_kcal', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('daily_biometrics', 'active_kcal')
    op.drop_column('daily_biometrics', 'steps')
