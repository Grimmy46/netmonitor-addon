"""per-device notification mute (maps keep updating). Idempotent.

Revision ID: 0042_device_mute
Revises: 0041_netbot_state
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_device_mute"
down_revision: Union[str, None] = "0041_netbot_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("devices")}
    if "muted_until" not in have:
        op.add_column("devices", sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    pass
