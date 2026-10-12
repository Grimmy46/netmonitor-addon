"""Teardown bot settings on signal_config (one JSONB blob). Idempotent.

Revision ID: 0046_teardown_bot
Revises: 0045_guest_link
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_teardown_bot"
down_revision: Union[str, None] = "0045_guest_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("signal_config")}
    if "teardown" not in have:
        op.add_column("signal_config", sa.Column("teardown", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE signal_config DROP COLUMN IF EXISTS teardown")
