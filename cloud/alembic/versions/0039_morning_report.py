"""daily opening report config on accounts. Idempotent.

Revision ID: 0039_morning_report
Revises: 0038_cameras
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_morning_report"
down_revision: Union[str, None] = "0038_cameras"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "morning_report" not in have:
        op.add_column("accounts", sa.Column("morning_report", postgresql.JSONB(), nullable=True))
    if "morning_report_last" not in have:
        op.add_column("accounts", sa.Column("morning_report_last", sa.String(), nullable=True))


def downgrade() -> None:
    pass
