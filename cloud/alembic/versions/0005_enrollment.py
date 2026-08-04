"""Enrollment: accounts.enrollment_pin + agents.claimed_at/machine_id.

Idempotent like the others.

Revision ID: 0005_enrollment
Revises: 0004_agent_pings
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_enrollment"
down_revision: Union[str, None] = "0004_agent_pings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "accounts", "enrollment_pin"):
        op.add_column("accounts", sa.Column("enrollment_pin", sa.String(), nullable=True))
    for col in ("claimed_at", "machine_id"):
        if not _has_column(insp, "agents", col):
            op.add_column("agents", sa.Column(col, sa.String(), nullable=True))

    # Backfill: agents created before enrollment already have a token — treat
    # them as claimed so they don't show as "unclaimed" in the new UI.
    op.execute(
        "UPDATE agents SET claimed_at = CAST(created_at AS VARCHAR) "
        "WHERE token_hash IS NOT NULL AND token_hash <> '' AND claimed_at IS NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for col in ("machine_id", "claimed_at"):
        if _has_column(insp, "agents", col):
            op.drop_column("agents", col)
    if _has_column(insp, "accounts", "enrollment_pin"):
        op.drop_column("accounts", "enrollment_pin")
