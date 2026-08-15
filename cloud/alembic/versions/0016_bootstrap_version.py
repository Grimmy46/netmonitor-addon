"""agents.bootstrap_version — the agent .exe capability level, so the server
only sends crash-isolated (device-I/O) commands to exes that can run a
worker subprocess. Idempotent.

Revision ID: 0016_bootstrap_version
Revises: 0015_agent_commands
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_bootstrap_version"
down_revision: Union[str, None] = "0015_agent_commands"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if not _has_column(insp, "agents", "bootstrap_version"):
        op.add_column("agents", sa.Column("bootstrap_version", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if _has_column(insp, "agents", "bootstrap_version"):
        op.drop_column("agents", "bootstrap_version")
