"""printer_events — a log of ticket-printer status CHANGES per station (for the
per-card history and the printer log report). Idempotent.

Revision ID: 0019_printer_events
Revises: 0018_agent_exe_update
Create Date: 2026-08-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_printer_events"
down_revision: Union[str, None] = "0018_agent_exe_update"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if "printer_events" not in insp.get_table_names():
        op.create_table(
            "printer_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("agent_id", sa.Uuid(),
                      sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("state", sa.String(), nullable=False, server_default=""),
            sa.Column("prev_state", sa.String(), nullable=True),
            sa.Column("raw", sa.String(), nullable=True),
            sa.Column("detail", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_printer_events_agent_id", "printer_events", ["agent_id"])
        op.create_index("ix_printer_events_created_at", "printer_events", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if "printer_events" in insp.get_table_names():
        op.drop_table("printer_events")
