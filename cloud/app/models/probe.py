"""Live-page probe targets and their time-series samples.

A ProbeTarget is something the Live landing page watches continuously:
  kind "ping"  → ICMP RTT to a host (the special target "gateway" means
                 "the probing kiosk's own default gateway"; the server-side
                 prober skips it — a cloud VM's gateway is meaningless).
  kind "http"  → HTTPS GET response time to a URL (any HTTP response counts
                 as up; ms is total request time).

Samples come from two vantages: the DESIGNATED kiosk agent (the on-lot truth,
agent_id set) and the server's own always-on prober (agent_id NULL — the cloud
view that keeps charts alive overnight when the lot powers down). High-volume
and short-lived: pruned after ~48h by the prober worker.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class ProbeTarget(Base, UUIDPk, Timestamps):
    __tablename__ = "probe_targets"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(default="ping")  # ping | http
    label: Mapped[str] = mapped_column(default="")
    target: Mapped[str] = mapped_column(default="")  # host/IP, URL, or "gateway"
    enabled: Mapped[bool] = mapped_column(default=True)
    sort: Mapped[int] = mapped_column(default=0)


class ProbeSample(Base, UUIDPk):
    __tablename__ = "probe_samples"
    __table_args__ = (Index("ix_probe_samples_target_ts", "target_id", "ts"),)

    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("probe_targets.id", ondelete="CASCADE")
    )
    # NULL = the server's own vantage; set = the designated kiosk agent.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), default=None
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ms: Mapped[float | None] = mapped_column(default=None)  # None = failed/timeout
