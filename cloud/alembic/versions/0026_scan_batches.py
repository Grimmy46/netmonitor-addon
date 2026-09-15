"""Scan Station batches: a counting run uploaded when its CSV was built. Idempotent.

Revision ID: 0026_scan_batches
Revises: 0025_status_events_site_dormant
Create Date: 2026-09-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_scan_batches"
down_revision: Union[str, None] = "0025_status_events_site_dormant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, t):
    return t in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if not _has_table(insp, "scan_batches"):
        op.create_table(
            "scan_batches",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("scanner", sa.String(), nullable=False, server_default=""),
            sa.Column("note", sa.String(), nullable=False, server_default=""),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
            sa.Column("csv_text", sa.String(), nullable=False, server_default=""),
            sa.Column("rows", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_scan_batches_created_at", "scan_batches", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind(); insp = sa.inspect(bind)
    if _has_table(insp, "scan_batches"):
        op.drop_index("ix_scan_batches_created_at", table_name="scan_batches")
        op.drop_table("scan_batches")
