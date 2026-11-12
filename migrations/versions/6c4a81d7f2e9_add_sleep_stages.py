"""add sleep_stages to daily_biometrics

Revision ID: 6c4a81d7f2e9
Revises: 3b7d52c8ea41
Create Date: 2026-11-12 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '6c4a81d7f2e9'
down_revision: str | None = '3b7d52c8ea41'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('daily_biometrics', sa.Column('sleep_stages', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('daily_biometrics', 'sleep_stages')
