"""Schemas for sites and devices served to the dashboard."""
import uuid

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


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    isp_name: str | None = None
    status: str = "unknown"
    device_count: int = 0
