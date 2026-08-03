"""Schemas for the UniFi integration endpoints."""
import uuid

from pydantic import BaseModel, ConfigDict, Field


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


# ── UniFi console (Network Integration API) ──────────────────────────────────
class UnifiConsoleIn(BaseModel):
    base_url: str = Field(
        min_length=5,
        description="Console hosting URL, e.g. https://<id>.unifi-hosting.ui.com",
    )
    api_key: str = Field(min_length=10, description="Console Network API key")
    label: str = "UniFi Console"
    verify_tls: bool = False


class UnifiConsoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    base_url: str
    key_hint: str | None = None
    verify_tls: bool = False
    last_synced_at: str | None = None
    last_error: str | None = None
    site_count: int = 0


class UnifiConsoleSyncResult(BaseModel):
    consoles: int
    sites: int
    devices: int
    errors: list[dict] = []
