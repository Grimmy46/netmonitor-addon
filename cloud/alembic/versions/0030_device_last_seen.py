"""Network map heartbeat: the controller's last-inform time per device. Idempotent.

Revision ID: 0030_device_last_seen
Revises: 0029_topology
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_device_last_seen"
down_revision: Union[str, None] = "0029_topology"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "unifi_last_seen" not in {c["name"] for c in insp.get_columns("devices")}:
        op.add_column("devices", sa.Column("unifi_last_seen", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "unifi_last_seen" in {c["name"] for c in insp.get_columns("devices")}:
        op.drop_column("devices", "unifi_last_seen")
