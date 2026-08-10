"""Live landing page: probe_targets + probe_samples + accounts.probe_agent_id.

Timestamps columns carry server_default=now() explicitly — lesson from 0010:
the baseline creates tables from model metadata (which has the defaults), but
incrementally-upgraded databases get THIS create_table, so it must match.
Idempotent throughout.

Revision ID: 0012_live_probes
Revises: 0011_push_sub_defaults
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0012_live_probes"
down_revision: Union[str, None] = "0011_push_sub_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, table: str) -> bool:
    return table in insp.get_table_names()


def _has_column(insp, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_table(insp, "probe_targets"):
        op.create_table(
            "probe_targets",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "account_id", UUID(as_uuid=True),
                sa.ForeignKey("accounts.id"), nullable=False,
            ),
            sa.Column("kind", sa.String(), nullable=False, server_default="ping"),
            sa.Column("label", sa.String(), nullable=False, server_default=""),
            sa.Column("target", sa.String(), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if not _has_table(insp, "probe_samples"):
        op.create_table(
            "probe_samples",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "target_id", UUID(as_uuid=True),
                sa.ForeignKey("probe_targets.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "agent_id", UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ms", sa.Float(), nullable=True),
        )
        op.create_index("ix_probe_samples_target_ts", "probe_samples", ["target_id", "ts"])

    if not _has_column(insp, "accounts", "probe_agent_id"):
        op.add_column(
            "accounts", sa.Column("probe_agent_id", UUID(as_uuid=True), nullable=True)
        )
        op.create_foreign_key(
            "fk_accounts_probe_agent", "accounts", "agents",
            ["probe_agent_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_column(insp, "accounts", "probe_agent_id"):
        op.drop_constraint("fk_accounts_probe_agent", "accounts", type_="foreignkey")
        op.drop_column("accounts", "probe_agent_id")
    if _has_table(insp, "probe_samples"):
        op.drop_table("probe_samples")
    if _has_table(insp, "probe_targets"):
        op.drop_table("probe_targets")
