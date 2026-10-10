"""Station offline/recovery history (agent_outages). Idempotent.

Revision ID: 0044_agent_outages
Revises: 0043_paper_counting
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_agent_outages"
down_revision: Union[str, None] = "0043_paper_counting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "agent_outages" not in set(insp.get_table_names()):
        op.create_table(
            "agent_outages",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),  # offline | off (shut down)
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_outages_started ON agent_outages (started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_outages_agent ON agent_outages (agent_id, ended_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_outages")
