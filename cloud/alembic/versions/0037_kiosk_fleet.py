"""kiosk LAN location, power-off state, weekly shutdown, fleet batches. Idempotent.

Revision ID: 0037_kiosk_fleet
Revises: 0036_signal
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_kiosk_fleet"
down_revision: Union[str, None] = "0036_signal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_COLS = [
    ("lan_ip", sa.String()), ("lan_mac", sa.String()), ("switch_mac", sa.String()),
    ("switch_port", sa.Integer()), ("switch_name", sa.String()),
    ("lan_seen_at", sa.DateTime(timezone=True)), ("powered_off_at", sa.DateTime(timezone=True)),
]
ACCOUNT_COLS = [("kiosk_shutdown", postgresql.JSONB()), ("kiosk_shutdown_last", sa.String())]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table, cols in (("agents", AGENT_COLS), ("accounts", ACCOUNT_COLS)):
        have = {c["name"] for c in insp.get_columns(table)}
        for name, typ in cols:
            if name not in have:
                op.add_column(table, sa.Column(name, typ, nullable=True))
    if "fleet_batches" not in insp.get_table_names():
        op.create_table(
            "fleet_batches",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("requested_by", sa.String(), nullable=False, server_default=""),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    pass
