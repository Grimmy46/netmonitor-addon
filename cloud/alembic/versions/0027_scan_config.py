"""Server-side Scan Station category schema. Idempotent.

Revision ID: 0027_scan_config
Revises: 0026_scan_batches
Create Date: 2026-09-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_scan_config"
down_revision: Union[str, None] = "0026_scan_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, t):
    return t in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if not _has_table(insp, "scan_config"):
        op.create_table(
            "scan_config",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if _has_table(insp, "scan_config"):
        op.drop_table("scan_config")
