"""Teardown mode: account-level alert pause during venue teardown. Idempotent.

Revision ID: 0023_teardown_mode
Revises: 0022_paper_roll_tracking
Create Date: 2026-08-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_teardown_mode"
down_revision: Union[str, None] = "0022_paper_roll_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("teardown_mode", sa.Boolean()),
    ("teardown_since", sa.DateTime(timezone=True)),
    ("teardown_auto_off_at", sa.DateTime(timezone=True)),
)


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, coltype in _COLS:
        if not _has_column(insp, "accounts", name):
            kwargs = {"server_default": sa.false()} if name == "teardown_mode" else {}
            op.add_column("accounts", sa.Column(name, coltype, nullable=True, **kwargs))


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, _ in _COLS:
        if _has_column(insp, "accounts", name):
            op.drop_column("accounts", name)
