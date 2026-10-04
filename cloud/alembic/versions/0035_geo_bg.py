"""geo_maps background image columns. Idempotent.

Revision ID: 0035_geo_bg
Revises: 0034_geo_zoom_float
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_geo_bg"
down_revision: Union[str, None] = "0034_geo_zoom_float"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLS = [
    ("bg_image", sa.LargeBinary()),
    ("bg_mime", sa.String()),
    ("bg_corners", postgresql.JSONB()),
    ("bg_opacity", sa.Float()),
]


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("geo_maps")}
    for name, typ in COLS:
        if name not in have:
            op.add_column("geo_maps", sa.Column(name, typ, nullable=True))
    if "bg_version" not in have:
        op.add_column("geo_maps", sa.Column("bg_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    pass
