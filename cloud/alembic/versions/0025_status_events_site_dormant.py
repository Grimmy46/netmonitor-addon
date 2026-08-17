"""Status-event log (offline order) + site dormancy fields. Idempotent.

Revision ID: 0025_status_events_site_dormant
Revises: 0024_site_teardown
Create Date: 2026-08-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_status_events_site_dormant"
down_revision: Union[str, None] = "0024_site_teardown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SITE_COLS = (
    ("manual_dormant", sa.Boolean(), sa.false()),
    ("offline_since", sa.DateTime(timezone=True), None),
)


def _has_table(insp, t):
    return t in insp.get_table_names()


def _has_column(insp, t, c):
    if t not in insp.get_table_names():
        return False
    return any(col["name"] == c for col in insp.get_columns(t))


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if not _has_table(insp, "status_events"):
        op.create_table(
            "status_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("site_id", sa.Uuid(),
                      sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=True),
            sa.Column("name", sa.String(), nullable=False, server_default=""),
            sa.Column("kind", sa.String(), nullable=False, server_default="site"),
            sa.Column("event", sa.String(), nullable=False, server_default=""),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_status_events_account_ts", "status_events", ["account_id", "ts"])
    for name, coltype, default in _SITE_COLS:
        if not _has_column(insp, "sites", name):
            kwargs = {"server_default": default} if default is not None else {}
            op.add_column("sites", sa.Column(name, coltype, nullable=True, **kwargs))


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    for name, _, _ in _SITE_COLS:
        if _has_column(insp, "sites", name):
            op.drop_column("sites", name)
    if _has_table(insp, "status_events"):
        op.drop_index("ix_status_events_account_ts", table_name="status_events")
        op.drop_table("status_events")
