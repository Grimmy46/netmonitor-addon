"""geo_maps: satellite MAP per site (placements + share link). Idempotent.

Revision ID: 0033_geo_maps
Revises: 0032_notification_log
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_geo_maps"
down_revision: Union[str, None] = "0032_notification_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "geo_maps" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "geo_maps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("center_lat", sa.Float(), nullable=True),
        sa.Column("center_lng", sa.Float(), nullable=True),
        sa.Column("zoom", sa.Integer(), nullable=True),
        sa.Column("placements", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("share_token", sa.String(), nullable=True, unique=True),
        sa.Column("share_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    if "geo_maps" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("geo_maps")
