"""agents printer status + printer alert state — continuous KPM180H monitoring.

The agent polls the ticket printer's real-time status (DLE EOT) and reports a
normalized state; the server stores the latest reading and runs a debounced
fault alert (paper out / cover open / error) alongside the kiosk-offline sweep.
Idempotent.

Revision ID: 0017_printer_status
Revises: 0016_bootstrap_version
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_printer_status"
down_revision: Union[str, None] = "0016_bootstrap_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("printer_status", sa.String()),           # ok | paper_out | cover_open | error | unknown
    ("printer_status_at", sa.String()),        # ISO ts of the last reading
    ("printer_raw", sa.String()),              # raw status byte(s) as hex
    ("printer_detail", sa.String()),           # human-readable decode
    ("printer_alert_state", sa.String()),      # None | pending | notified
    ("printer_alert_state_at", sa.DateTime(timezone=True)),
)


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, coltype in _COLS:
        if not _has_column(insp, "agents", name):
            op.add_column("agents", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, _ in _COLS:
        if _has_column(insp, "agents", name):
            op.drop_column("agents", name)
