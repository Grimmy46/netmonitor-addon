"""per-closure reopen grace + nightly auto-close marker. Idempotent.

Revision ID: 0040_nightly_close
Revises: 0039_morning_report
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_nightly_close"
down_revision: Union[str, None] = "0039_morning_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "closure_grace_min" not in have:
        op.add_column("accounts", sa.Column("closure_grace_min", sa.Integer(), nullable=True))
    if "autoclose_last" not in have:
        op.add_column("accounts", sa.Column("autoclose_last", sa.String(), nullable=True))


def downgrade() -> None:
    pass
