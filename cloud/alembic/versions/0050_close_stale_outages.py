"""Close station outages that were never ended (data fix). Idempotent.

A station that came back used to be marked online by its own report, so the
sweep never saw offline -> online and the outage row stayed open forever.
Each open row is ended at the station's first ping after the outage began
(minute rollups; raw pings for the last 2 days),
capped at the start of its next outage; rows for stations online right now
with no ping found are ended now. Rows for stations still offline stay open.

Revision ID: 0050_close_stale_outages
Revises: 0049_link_health
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0050_close_stale_outages"
down_revision: Union[str, None] = "0049_link_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        WITH o AS (
            SELECT o.id, o.agent_id, o.started_at,
                   (SELECT min(n.started_at) FROM agent_outages n
                     WHERE n.agent_id = o.agent_id AND n.started_at > o.started_at) AS next_start
              FROM agent_outages o
             WHERE o.ended_at IS NULL
        ), b AS (
            SELECT o.id, o.agent_id, o.next_start,
                   coalesce(
                       (SELECT min(r.bucket) FROM ping_rollup_1m r
                         WHERE r.agent_id = o.agent_id AND r.bucket >= o.started_at + interval '1 minute'
                           AND (o.next_start IS NULL OR r.bucket <= o.next_start)),
                       (SELECT min(p.ts) FROM ping_samples p
                         WHERE o.started_at > now() - interval '2 days'
                           AND p.agent_id = o.agent_id AND p.ts > o.started_at + interval '30 seconds'
                           AND (o.next_start IS NULL OR p.ts <= o.next_start))) AS back
              FROM o
        )
        UPDATE agent_outages t
           SET ended_at = coalesce(b.back, b.next_start,
                                   CASE WHEN a.status = 'online' THEN now() END)
          FROM b JOIN agents a ON a.id = b.agent_id
         WHERE t.id = b.id AND t.ended_at IS NULL
    """)


def downgrade() -> None:
    pass
