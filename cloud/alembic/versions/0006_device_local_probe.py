"""Local reachability from on-site agents: devices.local_reachable/local_rtt_ms/
local_checked_at.

An on-site agent pings each device on the LAN and reports whether it answered.
This is independent of UniFi's is_online, which enables the "up in the controller
but not reachable" (unreachable) state. Idempotent like 0003 — only adds columns
that aren't already present.

Revision ID: 0006_device_local_probe
Revises: 0005_enrollment
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_device_local_probe"
down_revision: Union[str, None] = "0005_enrollment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "devices", "local_reachable"):
        op.add_column("devices", sa.Column("local_reachable", sa.Boolean(), nullable=True))
    if not _has_column(insp, "devices", "local_rtt_ms"):
        op.add_column("devices", sa.Column("local_rtt_ms", sa.Float(), nullable=True))
    if not _has_column(insp, "devices", "local_checked_at"):
        op.add_column(
            "devices", sa.Column("local_checked_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for col in ("local_checked_at", "local_rtt_ms", "local_reachable"):
        if _has_column(insp, "devices", col):
            op.drop_column("devices", col)
