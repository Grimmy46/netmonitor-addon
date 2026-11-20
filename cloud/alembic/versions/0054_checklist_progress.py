"""Setup checklist progress, stored apart from the plan JSON so a plan save
can't wipe it: site_plans.checklist_progress (JSONB, keyed by item id).
Idempotent.

Revision ID: 0054_checklist_progress
Revises: 0053_bot_photos
Create Date: 2026-11-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_checklist_progress"
down_revision: Union[str, None] = "0053_bot_photos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("site_plans")}
    if "checklist_progress" not in cols:
        op.add_column("site_plans", sa.Column("checklist_progress", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE site_plans DROP COLUMN IF EXISTS checklist_progress")
