"""Paper counting: printed-cm counter, near-end sensor, roll-change log, daily
print totals. Idempotent.

Revision ID: 0043_paper_counting
Revises: 0042_device_mute
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_paper_counting"
down_revision: Union[str, None] = "0042_device_mute"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("agents")}
    for name, typ in (("printer_paper_cm", sa.Integer()), ("printer_roll_start_cm", sa.Integer()),
                      ("printer_cm_per_roll", sa.Float()), ("printer_near_end", sa.Boolean()),
                      ("printer_near_end_since", _TS), ("printer_out_since", _TS)):
        if name not in have:
            op.add_column("agents", sa.Column(name, typ, nullable=True))
    tables = set(insp.get_table_names())
    if "printer_rolls" not in tables:
        op.create_table(
            "printer_rolls",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("how", sa.String(16), nullable=False),          # ran_out | swapped | manual | inferred
            sa.Column("out_seconds", sa.Integer(), nullable=True),     # how long it sat empty
            sa.Column("prev_roll_tickets", sa.Integer(), nullable=True),
            sa.Column("prev_roll_cm", sa.Integer(), nullable=True),
            sa.Column("prev_roll_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("cut_count", sa.Integer(), nullable=True),
            sa.Column("paper_cm", sa.Integer(), nullable=True),
            sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_printer_rolls_agent_created", "printer_rolls", ["agent_id", "created_at"])
    if "printer_daily" not in tables:
        op.create_table(
            "printer_daily",
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("day", sa.Date(), primary_key=True),             # show-local (America/Los_Angeles)
            sa.Column("first_cuts", sa.Integer(), nullable=True),
            sa.Column("last_cuts", sa.Integer(), nullable=True),
            sa.Column("first_cm", sa.Integer(), nullable=True),
            sa.Column("last_cm", sa.Integer(), nullable=True),
            sa.Column("prev_last_cuts", sa.Integer(), nullable=True),  # previous day's close (continuity)
            sa.Column("prev_last_cm", sa.Integer(), nullable=True),
            sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    pass
