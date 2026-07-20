"""Schemas for the UniFi integration endpoints."""
from pydantic import BaseModel, Field


class UnifiKeyIn(BaseModel):
    api_key: str = Field(min_length=10, description="UniFi Site Manager API key")
    label: str = "UniFi Site Manager"


class UnifiKeyStatus(BaseModel):
    configured: bool
    label: str | None = None
    key_hint: str | None = None  # e.g. "…EHCH"
    last_synced_at: str | None = None


class UnifiSyncResult(BaseModel):
    sites: int
    devices: int
    metrics: int
