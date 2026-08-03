"""Track device offline history: devices.offline_since + devices.last_online_at.

Powers "down since …" and dormant classification. Idempotent like 0002 — only
adds columns that aren't already present (a fresh create_all baseline already
has them).

Revision ID: 0003_device_offline_history
Revises: 0002_unifi_console
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_device_offline_history"
down_revision: Union[str, None] = "0002_unifi_console"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "devices", "offline_since"):
        op.add_column("devices", sa.Column("offline_since", sa.DateTime(timezone=True), nullable=True))
    if not _has_column(insp, "devices", "last_online_at"):
        op.add_column("devices", sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_column(insp, "devices", "last_online_at"):
        op.drop_column("devices", "last_online_at")
    if _has_column(insp, "devices", "offline_since"):
        op.drop_column("devices", "offline_since")
