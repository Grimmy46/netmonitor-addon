"""An account is the top-level tenant (multi-tenant-ready from day one)."""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Account(Base, UUIDPk, Timestamps):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(default="RCS Tech")
    # Short code kiosks type on first run to claim a station (see agent enroll).
    enrollment_pin: Mapped[str | None] = mapped_column(default=None)
    # Dashboard admin PIN: gates destructive/config actions (Settings, station
    # management, keys) so shared dashboard access can't change things. None =
    # not set yet (bootstrap: everything open until the owner creates one).
    admin_pin: Mapped[str | None] = mapped_column(default=None)

    # The kiosk agent designated as the Live page's on-lot probe vantage.
    probe_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL", use_alter=True), default=None
    )

    # VAPID keypair for Web Push (generated lazily on first use; base64url raw).
    # The public key goes to browsers; the private key never leaves the server.
    vapid_public_key: Mapped[str | None] = mapped_column(default=None)
    vapid_private_key: Mapped[str | None] = mapped_column(default=None)

    users: Mapped[list["User"]] = relationship(back_populates="account")  # noqa: F821
    sites: Mapped[list["Site"]] = relationship(back_populates="account")  # noqa: F821
