"""WAN brownout incident log.

A "brownout" is when the internet uplink degrades (external targets show loss
or high latency) while the local LAN/gateway stays healthy — i.e. the problem
is upstream of the lot (the ISP / WAN), not our own network. Each confirmed
event opens one row here; it stays open (ended_at is NULL) until the on-lot
vantage sees the internet healthy again for the clear window. This is the
evidence trail for the ISP (Spectrum) conversation.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class WanIncident(Base, UUIDPk, Timestamps):
    __tablename__ = "wan_incidents"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String, default="brownout")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NULL while the incident is still ongoing.
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Worst readings observed on-lot over the life of the incident.
    peak_loss_pct: Mapped[float | None] = mapped_column(Float, default=None)
    peak_latency_ms: Mapped[float | None] = mapped_column(Float, default=None)
    worst_target: Mapped[str | None] = mapped_column(String, default=None)
    detail: Mapped[str | None] = mapped_column(String, default=None)

    # Internal recovery debounce: set when the internet first looks healthy
    # again; the incident only closes once it stays healthy for the clear window.
    clearing_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    __table_args__ = (
        Index("ix_wan_incidents_account_started", "account_id", "started_at"),
    )
