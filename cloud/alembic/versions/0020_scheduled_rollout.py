"""Scheduled full agent-exe rollout + dashboard notice (account-level). Idempotent.

Revision ID: 0020_scheduled_rollout
Revises: 0019_printer_events
Create Date: 2026-08-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_scheduled_rollout"
down_revision: Union[str, None] = "0019_printer_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("exe_rollout_at", sa.DateTime(timezone=True)),
    ("rollout_notice", sa.String()),
    ("rollout_notice_at", sa.DateTime(timezone=True)),
)


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, coltype in _COLS:
        if not _has_column(insp, "accounts", name):
            op.add_column("accounts", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, _ in _COLS:
        if _has_column(insp, "accounts", name):
            op.drop_column("accounts", name)
