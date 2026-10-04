"""notification_log: every push attempt. Idempotent.

Revision ID: 0032_notification_log
Revises: 0031_closure
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_notification_log"
down_revision: Union[str, None] = "0031_closure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "notification_log" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "notification_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("body", sa.String(), nullable=False, server_default=""),
        sa.Column("tag", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("devices", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notification_log_created_at", "notification_log", ["created_at"])


def downgrade() -> None:
    if "notification_log" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("notification_log")
