"""Self-building network map: per-device uplinks + per-site WAN link status. Idempotent.

Revision ID: 0029_topology
Revises: 0028_ping_rollups
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_topology"
down_revision: Union[str, None] = "0028_ping_rollups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEVICE_COLS = [
    ("uplink_mac", sa.String()),            # MAC of the device we plug into
    ("uplink_port", sa.Integer()),          # port number on that parent
    ("local_port", sa.Integer()),           # our own port that carries the uplink
    ("uplink_type", sa.String()),           # wire | wireless
    ("uplink_speed_mbps", sa.Integer()),
    ("uplink_depth", sa.Integer()),         # hops from the gateway
    ("ports_up", sa.Integer()),
    ("ports_total", sa.Integer()),
    ("topology_at", sa.DateTime(timezone=True)),
]
SITE_COLS = [
    ("unifi_site_ref", sa.String()),        # classic API site name, e.g. "default"
    ("wan_status", postgresql.JSONB()),     # {gateway, links:[{key,up,ip,...}]}
    ("wan_status_at", sa.DateTime(timezone=True)),
    ("wan_alert_state", postgresql.JSONB()),  # {"WAN1": {"state": "ok", "at": iso}}
]


def _cols(insp, t):
    return {c["name"] for c in insp.get_columns(t)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = _cols(insp, "devices")
    for name, typ in DEVICE_COLS:
        if name not in have:
            op.add_column("devices", sa.Column(name, typ, nullable=True))
    if "ix_devices_uplink_mac" not in {i["name"] for i in insp.get_indexes("devices")}:
        op.create_index("ix_devices_uplink_mac", "devices", ["uplink_mac"])
    have = _cols(insp, "sites")
    for name, typ in SITE_COLS:
        if name not in have:
            op.add_column("sites", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "ix_devices_uplink_mac" in {i["name"] for i in insp.get_indexes("devices")}:
        op.drop_index("ix_devices_uplink_mac", table_name="devices")
    have = _cols(insp, "devices")
    for name, _ in DEVICE_COLS:
        if name in have:
            op.drop_column("devices", name)
    have = _cols(insp, "sites")
    for name, _ in SITE_COLS:
        if name in have:
            op.drop_column("sites", name)
