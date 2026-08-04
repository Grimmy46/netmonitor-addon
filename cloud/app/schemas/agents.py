"""Schemas for site agents: registration, the report they push, and read views."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, description="Human label, e.g. 'Main Kiosk 3'")
    site_id: uuid.UUID | None = None


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    site_id: uuid.UUID | None = None
    site_name: str | None = None
    status: str = "pending"  # online | offline | pending
    online: bool = False
    claimed: bool = False       # a kiosk has enrolled as this station
    machine_id: str | None = None
    version: str | None = None
    hostname: str | None = None
    os: str | None = None
    last_ip: str | None = None
    last_target: str | None = None
    last_seen_at: str | None = None
    latest_rtt_ms: float | None = None


# ── enrollment (kiosk first-run station picker, PIN-gated) ───────────────────
class EnrollmentPinOut(BaseModel):
    pin: str


class EnrollStationsIn(BaseModel):
    pin: str


class EnrollStationOut(BaseModel):
    id: uuid.UUID
    name: str
    claimed: bool = False


class EnrollClaimIn(BaseModel):
    pin: str
    station_id: uuid.UUID
    hostname: str | None = None
    machine_id: str | None = None


class EnrollAddIn(BaseModel):
    pin: str
    name: str = Field(min_length=1)
    hostname: str | None = None
    machine_id: str | None = None


class EnrollResult(BaseModel):
    token: str
    name: str


# ── Agent → server report ────────────────────────────────────────────────────
class PingSampleIn(BaseModel):
    ts: float | None = None       # epoch seconds (agent clock); server falls back to now
    rtt: float | None = None      # round-trip ms; null = lost ping
    gw: float | None = None       # optional gateway control ping (ms)


class AgentReport(BaseModel):
    target: str = ""
    gateway: str = ""
    hostname: str | None = None
    os: str | None = None
    agent_version: str | None = None
    samples: list[PingSampleIn] = []


class AgentReportResult(BaseModel):
    ok: bool = True
    stored: int = 0
    commands: list[dict] = []  # Phase 3: pending commands for the agent to run


class PingPoint(BaseModel):
    ts: datetime
    rtt_ms: float | None = None
    gateway_rtt_ms: float | None = None
