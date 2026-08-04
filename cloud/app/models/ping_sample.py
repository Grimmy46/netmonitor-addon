"""Time-series ping samples pushed by a site agent.

Append-only. `rtt_ms` is null for a lost ping; `gateway_rtt_ms` is the optional
local-gateway control ping (host-vs-network diagnosis, same as the ping tool).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import UUIDPk


class PingSample(Base, UUIDPk):
    __tablename__ = "ping_samples"

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    target: Mapped[str] = mapped_column(default="")
    rtt_ms: Mapped[float | None] = mapped_column(Float, default=None)  # null = lost
    gateway_rtt_ms: Mapped[float | None] = mapped_column(Float, default=None)

    __table_args__ = (Index("ix_ping_samples_agent_ts", "agent_id", "ts"),)
