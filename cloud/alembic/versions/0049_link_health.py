"""Link health (degraded links) on devices + IP check results on sites. Idempotent.

Revision ID: 0049_link_health
Revises: 0048_paper_vision
Create Date: 2026-10-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_link_health"
down_revision: Union[str, None] = "0048_paper_vision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def _add(insp, table, col):
    if col.name not in {c["name"] for c in insp.get_columns(table)}:
        op.add_column(table, col)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    _add(insp, "devices", sa.Column("unifi_state", sa.Integer(), nullable=True))
    _add(insp, "devices", sa.Column("link_status", sa.String(16), nullable=True))   # ok | degraded
    _add(insp, "devices", sa.Column("link_reason", sa.String(), nullable=True))
    _add(insp, "devices", sa.Column("link_since", _TS, nullable=True))
    _add(insp, "devices", sa.Column("link_errors", sa.BigInteger(), nullable=True))
    _add(insp, "devices", sa.Column("link_notified_at", _TS, nullable=True))
    _add(insp, "sites", sa.Column("ip_issues", postgresql.JSONB(), nullable=True))
    _add(insp, "sites", sa.Column("ip_issues_at", _TS, nullable=True))


def downgrade() -> None:
    for c in ("unifi_state", "link_status", "link_reason", "link_since", "link_errors", "link_notified_at"):
        op.execute(f"ALTER TABLE devices DROP COLUMN IF EXISTS {c}")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS ip_issues")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS ip_issues_at")
