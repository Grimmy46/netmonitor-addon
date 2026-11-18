"""Teardown/setup bot photos: what was read off each photo and where the file
is, plus the text summary that replaces the file after 90 days. Idempotent.

Revision ID: 0053_bot_photos
Revises: 0050_close_stale_outages
Create Date: 2026-11-18

NOTE for the merge: 0051 (event mode) and 0052 come first. Point
down_revision at the 0052 revision when merging; nothing else depends on it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053_bot_photos"
down_revision: Union[str, None] = "0050_close_stale_outages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "bot_photos" not in set(insp.get_table_names()):
        op.create_table(
            "bot_photos",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("sent_at", _TS, nullable=False),
            sa.Column("sender", sa.String(120), nullable=False, server_default=""),
            sa.Column("mode", sa.String(16), nullable=False, server_default="return"),
            sa.Column("caption", sa.Text(), nullable=False, server_default=""),
            sa.Column("path", sa.String(), nullable=True),
            sa.Column("mime", sa.String(40), nullable=False, server_default="image/jpeg"),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(64), nullable=True),
            sa.Column("tags_read", postgresql.JSONB(), nullable=True),
            sa.Column("labels", postgresql.JSONB(), nullable=True),
            sa.Column("qr", postgresql.JSONB(), nullable=True),
            sa.Column("ocr_confidence", sa.Float(), nullable=True),
            sa.Column("ocr_ms", sa.Integer(), nullable=True),
            sa.Column("matched_tag", sa.String(32), nullable=True),
            sa.Column("matched_device", sa.String(200), nullable=True),
            sa.Column("matched_via", sa.String(40), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("summarized_at", _TS, nullable=True),
            sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        )
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("bot_photos")}
    if "ix_bot_photos_sent_at" not in idx:
        op.create_index("ix_bot_photos_sent_at", "bot_photos", ["sent_at"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bot_photos")
