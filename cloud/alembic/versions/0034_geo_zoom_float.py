"""geo_maps.zoom -> float (fractional zoom). Idempotent.

Revision ID: 0034_geo_zoom_float
Revises: 0033_geo_maps
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_geo_zoom_float"
down_revision: Union[str, None] = "0033_geo_maps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns("geo_maps")}
    if "zoom" in cols and not isinstance(cols["zoom"]["type"], sa.Float):
        op.alter_column("geo_maps", "zoom", type_=sa.Float(), postgresql_using="zoom::double precision")


def downgrade() -> None:
    pass
