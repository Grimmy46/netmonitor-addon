"""Per-site scheduled teardown + keep-monitored flags. Idempotent.

Revision ID: 0024_site_teardown
Revises: 0023_teardown_mode
Create Date: 2026-08-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_site_teardown"
down_revision: Union[str, None] = "0023_teardown_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SITE_COLS = (
    ("teardown_scheduled_at", sa.DateTime(timezone=True), None),
    ("teardown_active", sa.Boolean(), sa.false()),
    ("teardown_since", sa.DateTime(timezone=True), None),
    ("teardown_auto_off_at", sa.DateTime(timezone=True), None),
    ("keep_monitored", sa.Boolean(), sa.false()),
)
_DEVICE_COLS = (
    ("keep_monitored", sa.Boolean(), sa.false()),
)


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def _add(insp, table, cols):
    for name, coltype, default in cols:
        if not _has_column(insp, table, name):
            kwargs = {"server_default": default} if default is not None else {}
            op.add_column(table, sa.Column(name, coltype, nullable=True, **kwargs))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    _add(insp, "sites", _SITE_COLS)
    _add(insp, "devices", _DEVICE_COLS)


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, _, _ in _SITE_COLS:
        if _has_column(insp, "sites", name):
            op.drop_column("sites", name)
    for name, _, _ in _DEVICE_COLS:
        if _has_column(insp, "devices", name):
            op.drop_column("devices", name)
