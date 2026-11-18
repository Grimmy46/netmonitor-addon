"""Event mode switch on accounts (setup | open | teardown | offseason). Idempotent.

The starting value is derived from the current state when the column is first
added: a closure with more than 24 h left -> offseason; teardown_mode on ->
teardown; otherwise open.

Revision ID: 0051_event_mode
Revises: 0050_close_stale_outages
Create Date: 2026-11-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_event_mode"
down_revision: Union[str, None] = "0050_close_stale_outages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("accounts")}
    fresh = "event_mode" not in have
    cols = [
        sa.Column("event_mode", sa.String(), nullable=False, server_default="open"),
        sa.Column("event_mode_since", _TS, nullable=True),
        sa.Column("event_mode_until", _TS, nullable=True),
        sa.Column("event_mode_by", sa.String(), nullable=True),
        sa.Column("event_name", sa.String(), nullable=True),
        sa.Column("event_venue", sa.String(), nullable=True),
    ]
    for c in cols:
        if c.name not in have:
            op.add_column("accounts", c)
    if fresh:
        # Only on the first run, so re-running never overwrites a chosen mode.
        op.execute("""
            UPDATE accounts SET
              event_mode = CASE
                WHEN closure_start IS NOT NULL AND closure_end IS NOT NULL
                     AND closure_start <= now() AND closure_end > now() + interval '24 hours'
                  THEN 'offseason'
                WHEN teardown_mode THEN 'teardown'
                ELSE 'open' END,
              event_mode_since = CASE
                WHEN closure_start IS NOT NULL AND closure_end IS NOT NULL
                     AND closure_start <= now() AND closure_end > now() + interval '24 hours'
                  THEN closure_start
                WHEN teardown_mode THEN coalesce(teardown_since, now())
                ELSE NULL END,
              event_mode_until = CASE
                WHEN closure_start IS NOT NULL AND closure_end IS NOT NULL
                     AND closure_start <= now() AND closure_end > now() + interval '24 hours'
                  THEN closure_end
                WHEN teardown_mode THEN teardown_auto_off_at
                ELSE NULL END,
              event_mode_by = 'migration'
        """)


def downgrade() -> None:
    for c in ("event_mode", "event_mode_since", "event_mode_until", "event_mode_by",
              "event_name", "event_venue"):
        op.execute(f"ALTER TABLE accounts DROP COLUMN IF EXISTS {c}")
