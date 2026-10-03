"""Ping-sample rollups (per-minute + per-hour) so raw samples can be pruned. Idempotent.

Revision ID: 0028_ping_rollups
Revises: 0027_scan_config
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_ping_rollups"
down_revision: Union[str, None] = "0027_scan_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("ping_rollup_1m", "ping_rollup_1h")


def _columns():
    return [
        sa.Column("agent_id", sa.Uuid(),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("bucket", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("n", sa.Integer(), nullable=False),          # samples sent
        sa.Column("ok", sa.Integer(), nullable=False),         # samples answered
        sa.Column("avg_rtt_ms", sa.Float(), nullable=True),
        sa.Column("min_rtt_ms", sa.Float(), nullable=True),
        sa.Column("max_rtt_ms", sa.Float(), nullable=True),
        sa.Column("avg_gw_ms", sa.Float(), nullable=True),
        sa.Column("gw_n", sa.Integer(), nullable=False, server_default="0"),
    ]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = set(insp.get_table_names())
    for t in TABLES:
        if t not in existing:
            op.create_table(t, *_columns())
            op.create_index(f"ix_{t}_bucket", t, ["bucket"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = set(insp.get_table_names())
    for t in TABLES:
        if t in existing:
            op.drop_index(f"ix_{t}_bucket", table_name=t)
            op.drop_table(t)
