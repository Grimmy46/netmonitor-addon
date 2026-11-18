"""Photos the teardown/setup bot received, kept on disk for a while.

The image file lives under settings.photo_dir (a volume); this row says what
the bot read off it and what it did. After photo_keep_days the daily retention
job fills `summary` with a text version of the row and deletes the file
(`path` becomes NULL, `summarized_at` is set).
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class BotPhoto(UUIDPk, Timestamps, Base):
    __tablename__ = "bot_photos"

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sender: Mapped[str] = mapped_column(String(120), default="")
    mode: Mapped[str] = mapped_column(String(16), default="return")      # return (teardown) | deploy (setup)
    caption: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str | None] = mapped_column(String, default=None)       # relative to photo_dir; NULL once deleted
    mime: Mapped[str] = mapped_column(String(40), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    tags_read: Mapped[list | None] = mapped_column(JSONB, default=None)   # 4-digit tags read off the photo
    labels: Mapped[list | None] = mapped_column(JSONB, default=None)      # UniFi labels read off the photo
    qr: Mapped[list | None] = mapped_column(JSONB, default=None)          # raw QR contents
    ocr_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    ocr_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    matched_tag: Mapped[str | None] = mapped_column(String(32), default=None)
    matched_device: Mapped[str | None] = mapped_column(String(200), default=None)  # UniFi name
    matched_via: Mapped[str | None] = mapped_column(String(40), default=None)      # caption | photo | qr | label | offline
    result: Mapped[str | None] = mapped_column(Text, default=None)        # the bot's reply
    summary: Mapped[str | None] = mapped_column(Text, default=None)       # written when the file is deleted
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
