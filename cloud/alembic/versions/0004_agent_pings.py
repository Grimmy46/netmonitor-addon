"""Agent self-report columns + ping_samples time-series.

Idempotent like 0002/0003: only creates what's missing (a fresh create_all
baseline already has these).

Revision ID: 0004_agent_pings
Revises: 0003_device_offline_history
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_agent_pings"
down_revision: Union[str, None] = "0003_device_offline_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for col in ("hostname", "os", "last_ip", "last_target"):
        if not _has_column(insp, "agents", col):
            op.add_column("agents", sa.Column(col, sa.String(), nullable=True))

    if not any(
        ix["name"] == "ix_agents_token_hash" for ix in insp.get_indexes("agents")
    ):
        op.create_index("ix_agents_token_hash", "agents", ["token_hash"])

    if not _has_table(insp, "ping_samples"):
        op.create_table(
            "ping_samples",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("target", sa.String(), nullable=False, server_default=""),
            sa.Column("rtt_ms", sa.Float(), nullable=True),
            sa.Column("gateway_rtt_ms", sa.Float(), nullable=True),
        )
        op.create_index("ix_ping_samples_agent_id", "ping_samples", ["agent_id"])
        op.create_index("ix_ping_samples_ts", "ping_samples", ["ts"])
        op.create_index("ix_ping_samples_agent_ts", "ping_samples", ["agent_id", "ts"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_table(insp, "ping_samples"):
        op.drop_table("ping_samples")
    if any(ix["name"] == "ix_agents_token_hash" for ix in insp.get_indexes("agents")):
        op.drop_index("ix_agents_token_hash", table_name="agents")
    for col in ("last_target", "last_ip", "os", "hostname"):
        if _has_column(insp, "agents", col):
            op.drop_column("agents", col)
