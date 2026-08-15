"""A local agent (Phase 2) — runs on-site and pushes active test results.

Only deployed to select sites. Registers via a pairing token; authenticates each
push with its per-agent token.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Agent(Base, UUIDPk, Timestamps):
    __tablename__ = "agents"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"), default=None)

    name: Mapped[str] = mapped_column(default="")
    # Station category shown as its own dashboard tab: "kiosk" | "ticketbox".
    station_group: Mapped[str] = mapped_column(default="kiosk")
    # Hash of the agent's auth token (never store the token itself).
    token_hash: Mapped[str] = mapped_column(index=True, default="")
    version: Mapped[str | None] = mapped_column(default=None)
    bootstrap_version: Mapped[str | None] = mapped_column(default=None)
    last_seen_at: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="pending")  # pending | online | offline

    # Reported by the agent on each check-in (self-describing).
    hostname: Mapped[str | None] = mapped_column(default=None)
    os: Mapped[str | None] = mapped_column(default=None)
    last_ip: Mapped[str | None] = mapped_column(default=None)
    last_target: Mapped[str | None] = mapped_column(default=None)

    # Enrollment: which machine claimed this station (locks it), and when.
    claimed_at: Mapped[str | None] = mapped_column(default=None)
    machine_id: Mapped[str | None] = mapped_column(default=None)

    # Alert sweep state machine (see Device.alert_state for the values).
    alert_state: Mapped[str | None] = mapped_column(default=None)
    alert_state_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Ticket-printer (KPM180H) monitoring: the agent polls the printer's
    # real-time status and reports a normalized state; the server keeps the
    # latest reading and runs its own debounced fault alert.
    printer_status: Mapped[str | None] = mapped_column(default=None)  # ok|paper_out|cover_open|error|unknown
    printer_status_at: Mapped[str | None] = mapped_column(default=None)  # ISO ts of last reading
    printer_raw: Mapped[str | None] = mapped_column(default=None)  # raw status byte(s) hex
    printer_detail: Mapped[str | None] = mapped_column(default=None)  # human decode
    printer_alert_state: Mapped[str | None] = mapped_column(default=None)  # None|pending|notified
    printer_alert_state_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
