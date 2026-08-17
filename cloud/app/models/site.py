"""A monitored site, mirrored from UniFi Site Manager (/sites)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Site(Base, UUIDPk, Timestamps):
    __tablename__ = "sites"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    # Which connected console this site was pulled from (null = Site Manager API).
    console_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("unifi_consoles.id"), index=True, default=None
    )
    # UniFi identifiers (stable identity across DHCP changes).
    unifi_host_id: Mapped[str | None] = mapped_column(index=True, default=None)
    unifi_site_id: Mapped[str | None] = mapped_column(index=True, default=None)

    name: Mapped[str] = mapped_column(default="")
    isp_name: Mapped[str | None] = mapped_column(default=None)
    gateway_mac: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="unknown")  # online | offline | unknown

    # Authoritative fleet counts + WAN health, straight from the /sites
    # `statistics` block (no device join needed to render the site cards).
    device_total: Mapped[int] = mapped_column(default=0)
    device_offline: Mapped[int] = mapped_column(default=0)
    wan_uptime_pct: Mapped[float | None] = mapped_column(default=None)

    # Site-down alerting state machine (see workers/alerts.py): None (never
    # seen online — dark/retired sites can't alert), "ok" (last seen online),
    # "pending" (offline, confirmation window running), "notified" (down push
    # sent — recovery push fires when it returns).
    alert_state: Mapped[str | None] = mapped_column(default=None)
    alert_state_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Position on the fleet site map (pixels). Null until the user drags it.
    map_x: Mapped[float | None] = mapped_column(default=None)
    map_y: Mapped[float | None] = mapped_column(default=None)

    # Per-site teardown: when a venue is packed up its alerts pause. A one-off
    # scheduled_at arms it; the sweep flips teardown_active on when that time
    # passes (auto-off is a safety expiry). keep_monitored marks a critical site
    # (Safety, Main office) that must KEEP alerting through any teardown — its
    # status comes from the UniFi API, not the local agents that go offline.
    teardown_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    teardown_active: Mapped[bool] = mapped_column(default=False)
    teardown_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    teardown_auto_off_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    keep_monitored: Mapped[bool] = mapped_column(default=False)

    # Dormancy (packed-up venues): manual_dormant parks a site out of the active
    # board; offline_since drives the automatic 48h rule. A site returns to active
    # automatically when it comes back online (offline_since clears).
    manual_dormant: Mapped[bool] = mapped_column(default=False)
    offline_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    account: Mapped["Account"] = relationship(back_populates="sites")  # noqa: F821
    devices: Mapped[list["Device"]] = relationship(back_populates="site")  # noqa: F821
