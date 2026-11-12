"""daily_biometrics.total_kcal (Fitbit total energy expenditure)

Revision ID: d3f5a7c9e1b2
Revises: c9d2e4f6a8b0
Create Date: 2026-11-13 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'd3f5a7c9e1b2'
down_revision: str | None = 'c9d2e4f6a8b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('daily_biometrics', sa.Column('total_kcal', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('daily_biometrics', 'total_kcal')
