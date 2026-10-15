"""Daily AP restart settings + state on accounts (one JSONB blob). Idempotent.

Revision ID: 0047_ap_restart
Revises: 0046_teardown_bot
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_ap_restart"
down_revision: Union[str, None] = "0046_teardown_bot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "ap_restart" not in have:
        op.add_column("accounts", sa.Column("ap_restart", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS ap_restart")
