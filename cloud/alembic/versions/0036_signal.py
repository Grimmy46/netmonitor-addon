"""signal_config + signal_messages. Idempotent.

Revision ID: 0036_signal
Revises: 0035_geo_bg
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_signal"
down_revision: Union[str, None] = "0035_geo_bg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "signal_config" not in have:
        op.create_table(
            "signal_config",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("number", sa.String(), nullable=True),
            sa.Column("watched", postgresql.JSONB(), nullable=False, server_default="{}"),
            sa.Column("alert_group_id", sa.String(), nullable=True),
            sa.Column("last_receive_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            *_ts(),
        )
    if "signal_messages" not in have:
        op.create_table(
            "signal_messages",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("group_id", sa.String(), nullable=False),
            sa.Column("group_name", sa.String(), nullable=False, server_default=""),
            sa.Column("sender", sa.String(), nullable=False, server_default=""),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ts_ms", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("attachments", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source", sa.String(), nullable=False, server_default="live"),
            sa.Column("dedupe", sa.String(), nullable=False),
            *_ts(),
            sa.UniqueConstraint("dedupe", name="uq_signal_messages_dedupe"),
        )
        op.create_index("ix_signal_messages_group_id", "signal_messages", ["group_id"])
        op.create_index("ix_signal_messages_sent_at", "signal_messages", ["sent_at"])


def downgrade() -> None:
    pass
