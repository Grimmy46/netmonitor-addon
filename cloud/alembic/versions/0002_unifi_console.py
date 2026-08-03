"""Add UniFi console connections (Network Integration API) + sites.console_id.

Idempotent by design: the 0001 baseline builds tables from live ORM metadata via
create_all, so on a brand-new database the `unifi_consoles` table and the
`sites.console_id` column already exist by the time this runs. On the existing
production database (which ran 0001 before these models existed) they don't.
We inspect first and only create what's missing, so the same migration is safe
in both cases.

Revision ID: 0002_unifi_console
Revises: 0001_baseline
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_unifi_console"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, column: str) -> bool:
    if not _has_table(insp, table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_table(insp, "unifi_consoles"):
        op.create_table(
            "unifi_consoles",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("label", sa.String(), nullable=False, server_default="UniFi Console"),
            sa.Column("base_url", sa.String(), nullable=False, server_default=""),
            sa.Column("encrypted_api_key", sa.String(), nullable=False, server_default=""),
            sa.Column("key_hint", sa.String(), nullable=False, server_default=""),
            sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_synced_at", sa.String(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _has_column(insp, "sites", "console_id"):
        op.add_column("sites", sa.Column("console_id", sa.Uuid(), nullable=True))
        op.create_index("ix_sites_console_id", "sites", ["console_id"])
        op.create_foreign_key(
            "fk_sites_console_id_unifi_consoles",
            "sites",
            "unifi_consoles",
            ["console_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if _has_column(insp, "sites", "console_id"):
        with op.batch_alter_table("sites") as batch:
            try:
                batch.drop_constraint("fk_sites_console_id_unifi_consoles", type_="foreignkey")
            except Exception:  # noqa: BLE001
                pass
        try:
            op.drop_index("ix_sites_console_id", table_name="sites")
        except Exception:  # noqa: BLE001
            pass
        op.drop_column("sites", "console_id")

    if _has_table(insp, "unifi_consoles"):
        op.drop_table("unifi_consoles")
