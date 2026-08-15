"""Station groups: agents.station_group ("kiosk" | "ticketbox") — powers the
Ticket Boxes dashboard tab. Idempotent.

Revision ID: 0014_station_groups
Revises: 0013_site_alerts
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_station_groups"
down_revision: Union[str, None] = "0013_site_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "agents", "station_group"):
        op.add_column(
            "agents",
            sa.Column("station_group", sa.String(), nullable=False, server_default="kiosk"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_column(insp, "agents", "station_group"):
        op.drop_column("agents", "station_group")
