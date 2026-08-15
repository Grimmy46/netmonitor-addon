"""Remote command channel: agent_commands — the queued, allow-listed,
audited commands a kiosk picks up in its report response and answers on
/agents/command-result. Timestamps carry explicit server_default (the 0010
lesson). Idempotent.

Revision ID: 0015_agent_commands
Revises: 0014_station_groups
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0015_agent_commands"
down_revision: Union[str, None] = "0014_station_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_commands" in insp.get_table_names():
        return
    op.create_table(
        "agent_commands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id", UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("args", JSONB(), nullable=True),
        # queued -> sent -> done | error   (sent = delivered in a report response)
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("requested_by", sa.String(), nullable=False, server_default=""),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_commands" in insp.get_table_names():
        op.drop_table("agent_commands")
