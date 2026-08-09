"""Web-push notifications: browser push subscriptions (per signed-in user),
VAPID keypair on the account (generated once, lazily), and per-entity alert
state on devices + agents so the alert sweep never double-notifies and can
send recovery notices. Idempotent: only adds what's missing.

Revision ID: 0010_push_notifications
Revises: 0009_manual_dormant
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010_push_notifications"
down_revision: Union[str, None] = "0009_manual_dormant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, table: str) -> bool:
    return table in insp.get_table_names()


def _has_column(insp, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_table(insp, "push_subscriptions"):
        op.create_table(
            "push_subscriptions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
            sa.Column("p256dh", sa.Text(), nullable=False),
            sa.Column("auth", sa.Text(), nullable=False),
            sa.Column("ua", sa.Text(), nullable=True),
            sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    for col in ("vapid_public_key", "vapid_private_key"):
        if not _has_column(insp, "accounts", col):
            op.add_column("accounts", sa.Column(col, sa.Text(), nullable=True))

    for table in ("devices", "agents"):
        if not _has_column(insp, table, "alert_state"):
            op.add_column(table, sa.Column("alert_state", sa.String(16), nullable=True))
        if not _has_column(insp, table, "alert_state_at"):
            op.add_column(
                table, sa.Column("alert_state_at", sa.DateTime(timezone=True), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ("devices", "agents"):
        for col in ("alert_state_at", "alert_state"):
            if _has_column(insp, table, col):
                op.drop_column(table, col)
    for col in ("vapid_private_key", "vapid_public_key"):
        if _has_column(insp, "accounts", col):
            op.drop_column("accounts", col)
    if _has_table(insp, "push_subscriptions"):
        op.drop_table("push_subscriptions")
