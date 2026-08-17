"""Predictive paper: cut-count + roll-tracking columns on agents. Idempotent.

Revision ID: 0022_paper_roll_tracking
Revises: 0021_wan_incidents
Create Date: 2026-08-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_paper_roll_tracking"
down_revision: Union[str, None] = "0021_wan_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("printer_cut_count", sa.Integer()),
    ("printer_cut_count_at", sa.DateTime(timezone=True)),
    ("printer_roll_start_cut", sa.Integer()),
    ("printer_roll_start_at", sa.DateTime(timezone=True)),
    ("printer_roll_partial", sa.Boolean()),
    ("printer_cuts_per_roll", sa.Float()),
    ("printer_low_alert_state", sa.String()),
    ("printer_low_alert_at", sa.DateTime(timezone=True)),
)


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, coltype in _COLS:
        if not _has_column(insp, "agents", name):
            # server_default only for the non-null boolean so existing rows fill in.
            kwargs = {"server_default": sa.true()} if name == "printer_roll_partial" else {}
            op.add_column("agents", sa.Column(name, coltype, nullable=True, **kwargs))


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, _ in _COLS:
        if _has_column(insp, "agents", name):
            op.drop_column("agents", name)
