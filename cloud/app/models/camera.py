"""Tapo cameras found on the show network (UniFi client list) and the one
shared camera login + relay-kiosk choice."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Camera(UUIDPk, Timestamps, Base):
    __tablename__ = "cameras"

    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"), default=None)
    slot: Mapped[int] = mapped_column(Integer, unique=True)       # go2rtc stream camNN
    mac: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    ip: Mapped[str | None] = mapped_column(String, default=None)
    ap_name: Mapped[str | None] = mapped_column(String, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CameraConfig(UUIDPk, Timestamps, Base):
    __tablename__ = "camera_config"

    username: Mapped[str | None] = mapped_column(String, default=None)
    password_enc: Mapped[str | None] = mapped_column(String, default=None)  # Fernet
    relay_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), default=None)
