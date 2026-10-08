"""NETBOT state (early-close check-ins, weather alerts sent, shortcut token). Idempotent.

Revision ID: 0041_netbot_state
Revises: 0040_nightly_close
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_netbot_state"
down_revision: Union[str, None] = "0040_nightly_close"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "netbot_state" not in have:
        op.add_column("accounts", sa.Column("netbot_state", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    pass
