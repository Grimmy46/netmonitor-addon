"""A network device, mirrored from UniFi Site Manager (/devices).

Identity is the UniFi device id / MAC — NOT the IP — so DHCP lease changes never
create duplicates or break history.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Device(Base, UUIDPk, Timestamps):
    __tablename__ = "devices"

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    unifi_device_id: Mapped[str | None] = mapped_column(index=True, default=None)
    mac: Mapped[str | None] = mapped_column(index=True, default=None)

    name: Mapped[str] = mapped_column(default="")
    model: Mapped[str | None] = mapped_column(default=None)
    device_type: Mapped[str | None] = mapped_column(default=None)  # switch | ap | gateway | other
    ip: Mapped[str | None] = mapped_column(default=None)  # current lease, refreshed each sync
    is_online: Mapped[bool | None] = mapped_column(default=None)

    # State-change tracking (server-side, not from UniFi): when the device was
    # first observed offline (cleared when it returns), and when it was last
    # seen online. `offline_since` drives "down since …" and dormant detection.
    offline_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_online_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Operator override: park this device in the Dormant tab regardless of how
    # long it's been offline (packed-up gear, spares). Effective dormancy is
    # manual_dormant OR the automatic offline-age rule.
    manual_dormant: Mapped[bool] = mapped_column(default=False)

    # Local reachability from an on-site agent actively pinging this device on the
    # LAN (by its current IP). This is independent of UniFi's `is_online`: a device
    # can be ONLINE in the controller yet not answer a LAN ping ("unreachable").
    # None = no agent has probed it (no-data).
    local_reachable: Mapped[bool | None] = mapped_column(default=None)
    local_rtt_ms: Mapped[float | None] = mapped_column(default=None)
    local_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Alert sweep state machine: None (healthy / nothing to say), "notified"
    # (down push sent — a recovery push goes out when it returns), "suppressed"
    # (part of a mass power-down; no individual pushes either way), or "stale"
    # (fault predates the alerting feature / was too old to alert on).
    alert_state: Mapped[str | None] = mapped_column(default=None)
    alert_state_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    site: Mapped["Site"] = relationship(back_populates="devices")  # noqa: F821
