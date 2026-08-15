"""Staged agent-exe self-update: agents.exe_rollout flag + agent_binaries table
(the uploaded bootstrapper the fleet downloads and swaps to). Idempotent.

Revision ID: 0018_agent_exe_update
Revises: 0017_printer_status
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_agent_exe_update"
down_revision: Union[str, None] = "0017_printer_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if not _has_column(insp, "agents", "exe_rollout"):
        op.add_column("agents", sa.Column("exe_rollout", sa.Boolean(),
                                          nullable=False, server_default=sa.false()))
    if "agent_binaries" not in insp.get_table_names():
        op.create_table(
            "agent_binaries",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("version", sa.String(), nullable=False, server_default=""),
            sa.Column("sha256", sa.String(), nullable=False, server_default=""),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("filename", sa.String(), nullable=False, server_default="NetMonAgent.exe"),
            sa.Column("data", sa.LargeBinary(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_agent_binaries_active", "agent_binaries", ["active"])


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if "agent_binaries" in insp.get_table_names():
        op.drop_table("agent_binaries")
    if _has_column(insp, "agents", "exe_rollout"):
        op.drop_column("agents", "exe_rollout")
