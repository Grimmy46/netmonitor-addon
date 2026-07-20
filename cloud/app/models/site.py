"""A monitored site, mirrored from UniFi Site Manager (/sites)."""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Site(Base, UUIDPk, Timestamps):
    __tablename__ = "sites"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    # UniFi identifiers (stable identity across DHCP changes).
    unifi_host_id: Mapped[str | None] = mapped_column(index=True, default=None)
    unifi_site_id: Mapped[str | None] = mapped_column(index=True, default=None)

    name: Mapped[str] = mapped_column(default="")
    isp_name: Mapped[str | None] = mapped_column(default=None)
    gateway_mac: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="unknown")  # online | offline | unknown

    account: Mapped["Account"] = relationship(back_populates="sites")  # noqa: F821
    devices: Mapped[list["Device"]] = relationship(back_populates="site")  # noqa: F821
