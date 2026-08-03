"""Schemas for sites and devices served to the dashboard."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    model: str | None = None
    device_type: str | None = None
    ip: str | None = None
    mac: str | None = None
    is_online: bool | None = None

    # State history + server-side classification.
    offline_since: datetime | None = None
    last_online_at: datetime | None = None
    down_seconds: int | None = None  # how long it's been offline
    dormant: bool = False  # offline longer than the dormant threshold


class DormantDeviceOut(BaseModel):
    """A dormant device shown in the fleet-wide Dormant tab (carries its site)."""
    id: uuid.UUID
    name: str
    model: str | None = None
    device_type: str | None = None
    ip: str | None = None
    mac: str | None = None
    site_id: uuid.UUID
    site_name: str
    offline_since: datetime | None = None
    down_seconds: int | None = None


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    isp_name: str | None = None
    status: str = "unknown"
    device_count: int = 0
    online_device_count: int = 0
    # Devices offline longer than the dormant threshold (packed-up / decommissioned).
    dormant_device_count: int = 0

    # Latest WAN/ISP health (from the most recent isp_metric row).
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    uptime_pct: float | None = None
    download_mbps: float | None = None
    upload_mbps: float | None = None

    # Saved position on the fleet site map (pixels; null = not yet placed).
    map_x: float | None = None
    map_y: float | None = None


class MetricPoint(BaseModel):
    ts: datetime
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    download_mbps: float | None = None
    upload_mbps: float | None = None
