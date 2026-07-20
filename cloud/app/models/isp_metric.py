"""Time-series WAN/ISP health per site, from UniFi /isp-metrics.

Kept as append-only rows; becomes a TimescaleDB hypertable when volume warrants.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import UUIDPk


class IspMetric(Base, UUIDPk):
    __tablename__ = "isp_metrics"

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    wan: Mapped[str] = mapped_column(default="primary")  # primary | secondary

    latency_ms: Mapped[float | None] = mapped_column(Float, default=None)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, default=None)
    download_mbps: Mapped[float | None] = mapped_column(Float, default=None)
    upload_mbps: Mapped[float | None] = mapped_column(Float, default=None)
    uptime_pct: Mapped[float | None] = mapped_column(Float, default=None)

    __table_args__ = (Index("ix_isp_metrics_site_ts", "site_id", "ts"),)
