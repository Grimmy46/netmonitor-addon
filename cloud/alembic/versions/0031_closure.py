"""Planned closure window on the account. Idempotent.

Revision ID: 0031_closure
Revises: 0030_device_last_seen
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_closure"
down_revision: Union[str, None] = "0030_device_last_seen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLS = [
    ("closure_start", sa.DateTime(timezone=True)),
    ("closure_end", sa.DateTime(timezone=True)),
    ("closure_note", sa.String()),
    ("closure_report_sent_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    for name, typ in COLS:
        if name not in have:
            op.add_column("accounts", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    for name, _ in COLS:
        if name in have:
            op.drop_column("accounts", name)
