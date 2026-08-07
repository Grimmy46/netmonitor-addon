"""SitePlanner cloud storage: site_plans table (plan JSON + aerial photo).

Idempotent like 0003/0006/0007 — creates the table only if absent.

Revision ID: 0008_site_plans
Revises: 0007_admin_pin
Create Date: 2026-08-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008_site_plans"
down_revision: Union[str, None] = "0007_admin_pin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "site_plans" in insp.get_table_names():
        return
    op.create_table(
        "site_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False, server_default="Site plan"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("data", JSONB(), nullable=False, server_default="{}"),
        sa.Column("aerial", sa.LargeBinary(), nullable=True),
        sa.Column("aerial_mime", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "site_plans" in insp.get_table_names():
        op.drop_table("site_plans")
