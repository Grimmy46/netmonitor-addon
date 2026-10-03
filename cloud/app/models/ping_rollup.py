"""Per-minute and per-hour rollups of ping_samples.

Written by the retention worker (app/workers/retention.py). Raw samples are
pruned after `ping_raw_retention_days`; these tables keep the long-term history
(minute rollups for `ping_minute_retention_days`, hour rollups forever).
`n` = pings sent, `ok` = pings answered, so loss = 1 - ok/n.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class _RollupCols:
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    n: Mapped[int] = mapped_column(Integer)
    ok: Mapped[int] = mapped_column(Integer)
    avg_rtt_ms: Mapped[float | None] = mapped_column(Float, default=None)
    min_rtt_ms: Mapped[float | None] = mapped_column(Float, default=None)
    max_rtt_ms: Mapped[float | None] = mapped_column(Float, default=None)
    avg_gw_ms: Mapped[float | None] = mapped_column(Float, default=None)
    gw_n: Mapped[int] = mapped_column(Integer, default=0)


class PingRollup1m(_RollupCols, Base):
    __tablename__ = "ping_rollup_1m"


class PingRollup1h(_RollupCols, Base):
    __tablename__ = "ping_rollup_1h"
