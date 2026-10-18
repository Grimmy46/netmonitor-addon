"""Camera paper vision: per-camera roll boxes (cameras.paper_rois) and a log of
camera readings / roll-change / jam-suspect events. Idempotent.

Revision ID: 0048_paper_vision
Revises: 0047_ap_restart
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_paper_vision"
down_revision: Union[str, None] = "0047_ap_restart"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "paper_rois" not in {c["name"] for c in insp.get_columns("cameras")}:
        op.add_column("cameras", sa.Column("paper_rois", postgresql.JSONB(), nullable=True))
    if "paper_vision" not in set(insp.get_table_names()):
        op.create_table(
            "paper_vision",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("camera_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True),
            sa.Column("kind", sa.String(16), nullable=False),        # reading | changed | jam_suspect
            sa.Column("level", sa.Float(), nullable=True),           # 0–100 %
            sa.Column("prev_level", sa.Float(), nullable=True),
            sa.Column("h_px", sa.Integer(), nullable=True),
            sa.Column("detail", sa.String(), nullable=True),
            sa.Column("image", sa.LargeBinary(), nullable=True),     # small JPEG crop (events only)
            sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_paper_vision_agent_created", "paper_vision", ["agent_id", "created_at"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_vision")
    op.execute("ALTER TABLE cameras DROP COLUMN IF EXISTS paper_rois")
