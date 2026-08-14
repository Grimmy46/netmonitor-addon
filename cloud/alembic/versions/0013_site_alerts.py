"""Site-down alerting: sites.alert_state + alert_state_at — the state machine
that pushes "site X is down" / "back online" notifications. Idempotent.

Revision ID: 0013_site_alerts
Revises: 0012_live_probes
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_site_alerts"
down_revision: Union[str, None] = "0012_live_probes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "sites", "alert_state"):
        op.add_column("sites", sa.Column("alert_state", sa.String(16), nullable=True))
    if not _has_column(insp, "sites", "alert_state_at"):
        op.add_column(
            "sites", sa.Column("alert_state_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for col in ("alert_state_at", "alert_state"):
        if _has_column(insp, "sites", col):
            op.drop_column("sites", col)
