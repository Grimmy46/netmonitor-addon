"""Guest view link token on accounts. Idempotent.

Revision ID: 0045_guest_link
Revises: 0044_agent_outages
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045_guest_link"
down_revision: Union[str, None] = "0044_agent_outages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "guest_token" not in have:
        op.add_column("accounts", sa.Column("guest_token", sa.String(64), nullable=True))
    if "guest_token_at" not in have:
        op.add_column("accounts", sa.Column("guest_token_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS guest_token")
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS guest_token_at")
