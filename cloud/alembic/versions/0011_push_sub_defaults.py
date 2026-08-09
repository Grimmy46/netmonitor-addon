"""Repair push_subscriptions timestamps: 0010's create_table omitted the
server-side now() defaults the Timestamps mixin expects, so every INSERT died
with a NOT NULL violation on databases upgraded incrementally (fresh installs
were fine — the 0001 baseline builds from model metadata, which HAS the
defaults; that's exactly why tests-from-empty missed it). Idempotent: SET
DEFAULT is safe to re-run, and rows can't exist yet on broken databases.

Revision ID: 0011_push_sub_defaults
Revises: 0010_push_notifications
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_push_sub_defaults"
down_revision: Union[str, None] = "0010_push_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "push_subscriptions" not in insp.get_table_names():
        return
    op.execute(
        "ALTER TABLE push_subscriptions ALTER COLUMN created_at SET DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE push_subscriptions ALTER COLUMN updated_at SET DEFAULT now()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "push_subscriptions" not in insp.get_table_names():
        return
    op.execute("ALTER TABLE push_subscriptions ALTER COLUMN created_at DROP DEFAULT")
    op.execute("ALTER TABLE push_subscriptions ALTER COLUMN updated_at DROP DEFAULT")
