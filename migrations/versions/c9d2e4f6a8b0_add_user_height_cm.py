"""users.height_cm (for BMR estimation)

Revision ID: c9d2e4f6a8b0
Revises: b7c4e8f21a95
Create Date: 2026-11-13 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c9d2e4f6a8b0'
down_revision: str | None = 'b7c4e8f21a95'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('height_cm', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'height_cm')
