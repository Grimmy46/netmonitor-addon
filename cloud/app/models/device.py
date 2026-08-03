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

    site: Mapped["Site"] = relationship(back_populates="devices")  # noqa: F821
