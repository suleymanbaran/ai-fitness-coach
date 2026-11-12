"""run_logs: source + external_id for idempotent Google Health sync

Revision ID: b7c4e8f21a95
Revises: a4f82c6d91e3
Create Date: 2026-11-13 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b7c4e8f21a95'
down_revision: str | None = 'a4f82c6d91e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('run_logs', sa.Column('source', sa.String(length=12), server_default='manual', nullable=False))
    op.add_column('run_logs', sa.Column('external_id', sa.String(length=200), nullable=True))
    op.create_index(
        'uq_run_logs_user_external', 'run_logs', ['user_id', 'external_id'],
        unique=True, postgresql_where=sa.text('external_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_run_logs_user_external', table_name='run_logs')
    op.drop_column('run_logs', 'external_id')
    op.drop_column('run_logs', 'source')
