"""Tapo cameras + camera login/relay config. Idempotent.

Revision ID: 0038_cameras
Revises: 0037_kiosk_fleet
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_cameras"
down_revision: Union[str, None] = "0037_kiosk_fleet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "cameras" not in tables:
        op.create_table(
            "cameras",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id"), nullable=True),
            sa.Column("slot", sa.Integer(), nullable=False, unique=True),
            sa.Column("mac", sa.String(), nullable=False, unique=True),
            sa.Column("name", sa.String(), nullable=False, server_default=""),
            sa.Column("model", sa.String(), nullable=False, server_default=""),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("ap_name", sa.String(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            *_ts(),
        )
    if "camera_config" not in tables:
        op.create_table(
            "camera_config",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("username", sa.String(), nullable=True),
            sa.Column("password_enc", sa.String(), nullable=True),
            sa.Column("relay_agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=True),
            *_ts(),
        )


def downgrade() -> None:
    pass
