"""Manual dormant: devices.manual_dormant — an operator can park a device in
the Dormant tab regardless of how long it's been offline (packed-up gear,
spares, decommissioned kit). Effective dormancy = manual flag OR the automatic
offline-age rule. Idempotent like the rest: only adds what's missing.

Revision ID: 0009_manual_dormant
Revises: 0008_site_plans
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_manual_dormant"
down_revision: Union[str, None] = "0008_site_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "devices", "manual_dormant"):
        op.add_column(
            "devices",
            sa.Column("manual_dormant", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_column(insp, "devices", "manual_dormant"):
        op.drop_column("devices", "manual_dormant")
