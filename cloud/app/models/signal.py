"""Signal: the bot is a *linked device* on the user's own Signal account
(like Signal Desktop), run by the signal-cli REST container. It reads the
group chats we pick (deploy log, hardware/equipment list) into an archive
the app can search and mine, and can optionally post alerts into a group."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class SignalConfig(UUIDPk, Timestamps, Base):
    """Single row."""
    __tablename__ = "signal_config"

    number: Mapped[str | None] = mapped_column(String, default=None)  # linked account (+1…)
    # {"<group_id>": {"name": "...", "role": "deploy" | "hardware" | "other"}}
    watched: Mapped[dict] = mapped_column(JSONB, default=dict)
    alert_group_id: Mapped[str | None] = mapped_column(String, default=None)
    last_receive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(String, default=None)


class SignalMessage(UUIDPk, Timestamps, Base):
    __tablename__ = "signal_messages"
    __table_args__ = (UniqueConstraint("dedupe", name="uq_signal_messages_dedupe"),)

    group_id: Mapped[str] = mapped_column(String, index=True)   # Signal internal id, or "import:<name>"
    group_name: Mapped[str] = mapped_column(String, default="")
    sender: Mapped[str] = mapped_column(String, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    body: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String, default="live")  # live | import
    dedupe: Mapped[str] = mapped_column(String)
