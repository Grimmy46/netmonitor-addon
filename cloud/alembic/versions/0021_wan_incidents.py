"""WAN brownout incident log + accounts.brownout_pending_at. Idempotent.

Revision ID: 0021_wan_incidents
Revises: 0020_scheduled_rollout
Create Date: 2026-08-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_wan_incidents"
down_revision: Union[str, None] = "0020_scheduled_rollout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, t):
    return t in insp.get_table_names()


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)

    if not _has_table(insp, "wan_incidents"):
        op.create_table(
            "wan_incidents",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_id", sa.Uuid(),
                      sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False, server_default="brownout"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("peak_loss_pct", sa.Float(), nullable=True),
            sa.Column("peak_latency_ms", sa.Float(), nullable=True),
            sa.Column("worst_target", sa.String(), nullable=True),
            sa.Column("detail", sa.String(), nullable=True),
            sa.Column("clearing_since", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_wan_incidents_account_started", "wan_incidents",
            ["account_id", "started_at"],
        )

    if not _has_column(insp, "accounts", "brownout_pending_at"):
        op.add_column(
            "accounts",
            sa.Column("brownout_pending_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if _has_column(insp, "accounts", "brownout_pending_at"):
        op.drop_column("accounts", "brownout_pending_at")
    if _has_table(insp, "wan_incidents"):
        op.drop_index("ix_wan_incidents_account_started", table_name="wan_incidents")
        op.drop_table("wan_incidents")
