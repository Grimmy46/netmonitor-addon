"""An account is the top-level tenant (multi-tenant-ready from day one)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
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

    # Scheduled full agent-exe rollout: when set, the alert sweep flags ALL claimed
    # stations for the exe self-update once this UTC time passes, then clears it and
    # posts a dashboard notice. Lets a full push be armed for an off-hours window.
    exe_rollout_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # A dashboard banner (e.g. "full rollout started"); shown until dismissed.
    rollout_notice: Mapped[str | None] = mapped_column(default=None)
    rollout_notice_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Teardown mode: while on, the alert sweep pauses ALL fault pushes (kiosks,
    # devices, sites, printers, low-paper) so packing up a venue doesn't storm
    # the operator. auto_off_at is a safety expiry so it can't silently mask
    # problems at the next venue if someone forgets to turn it off.
    teardown_mode: Mapped[bool] = mapped_column(default=False)
    teardown_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    teardown_auto_off_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # WAN brownout confirm timer: set when the internet first looks degraded
    # (while the LAN is fine) and no incident is open yet; once it persists past
    # the confirm window the alert sweep opens a WanIncident and clears this.
    brownout_pending_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    users: Mapped[list["User"]] = relationship(back_populates="account")  # noqa: F821
    sites: Mapped[list["Site"]] = relationship(back_populates="account")  # noqa: F821
