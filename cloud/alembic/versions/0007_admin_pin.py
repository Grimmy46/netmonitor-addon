"""Dashboard admin PIN: accounts.admin_pin.

Gates destructive/config actions behind a PIN so shared dashboard access can't
change or delete things. Idempotent like 0003/0006.

Revision ID: 0007_admin_pin
Revises: 0006_device_local_probe
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_admin_pin"
down_revision: Union[str, None] = "0006_device_local_probe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, column: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _has_column(insp, "accounts", "admin_pin"):
        op.add_column("accounts", sa.Column("admin_pin", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_column(insp, "accounts", "admin_pin"):
        op.drop_column("accounts", "admin_pin")
