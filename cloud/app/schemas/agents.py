"""Schemas for site agents: registration, the report they push, and read views."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, description="Human label, e.g. 'Main Kiosk 3'")
    site_id: uuid.UUID | None = None
    station_group: str = "kiosk"  # kiosk | ticketbox


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    site_id: uuid.UUID | None = None
    site_name: str | None = None
    station_group: str = "kiosk"
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
    bootstrap_version: str | None = None  # the .exe capability level (self-update target)
    exe_rollout: bool = False             # is this station opted into the exe self-update?
    # Ticket-printer status (present only when a printer is detected & polled).
    printer_status: str | None = None  # ok | paper_out | cover_open | error | unknown
    printer_status_at: str | None = None
    printer_detail: str | None = None
    printer_raw: str | None = None
    # Predictive paper (present only once a cut count has been read).
    printer_cut_count: int | None = None
    printer_roll_percent: float | None = None    # % of the roll used (0–100)
    printer_cuts_remaining: int | None = None     # est. tickets left this roll
    printer_cuts_per_roll: float | None = None    # effective yield (learned or seed)
    printer_roll_learned: bool = False            # yield measured from a real run-out?
    printer_roll_partial: bool = False            # anchor set mid-roll (estimate only)


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


class BulkStationsIn(BaseModel):
    names: list[str]
    station_group: str = "kiosk"


class BulkResult(BaseModel):
    created: int
    skipped: int


# ── Agent → server report ────────────────────────────────────────────────────
class PingSampleIn(BaseModel):
    ts: float | None = None       # epoch seconds (agent clock); server falls back to now
    rtt: float | None = None      # round-trip ms; null = lost ping
    gw: float | None = None       # optional gateway control ping (ms)


class PrinterStatusIn(BaseModel):
    """The agent's latest ticket-printer reading, attached to each report."""
    present: bool = False                # a USB printer interface was found
    state: str | None = None             # ok | paper_out | cover_open | error | unknown
    raw: str | None = None               # raw status byte(s) as hex
    detail: str | None = None            # human-readable decode
    cut_count: int | None = None         # lifetime cut count (≈ tickets), for paper tracking
    paper_remaining_cm: int | None = None  # printer's own paper-remaining gauge (cm), if programmed


class AgentReport(BaseModel):
    target: str = ""
    gateway: str = ""
    hostname: str | None = None
    os: str | None = None
    agent_version: str | None = None
    bootstrap_version: str | None = None
    printer: PrinterStatusIn | None = None
    samples: list[PingSampleIn] = []


class AgentReportResult(BaseModel):
    ok: bool = True
    stored: int = 0
    commands: list[dict] = []  # Phase 3: pending commands for the agent to run


class PingPoint(BaseModel):
    ts: datetime
    rtt_ms: float | None = None
    gateway_rtt_ms: float | None = None


# ── local LAN probing (agent pings the site's UniFi devices on the LAN) ───────
class ProbeTarget(BaseModel):
    """One device for the agent to ping locally."""
    id: uuid.UUID
    name: str
    ip: str
    mac: str | None = None


class ProbeTargetsOut(BaseModel):
    site_id: uuid.UUID | None = None
    site_name: str | None = None
    interval: int = 120  # suggested seconds between full sweeps
    targets: list[ProbeTarget] = []


class DeviceProbeIn(BaseModel):
    id: uuid.UUID                 # the device id from /targets
    reachable: bool               # did it answer a LAN ping?
    rtt_ms: float | None = None   # round-trip ms if reachable


class DeviceProbeReport(BaseModel):
    results: list[DeviceProbeIn] = []


class DeviceProbeResult(BaseModel):
    ok: bool = True
    updated: int = 0


# ── agent exe self-update (staged rollout) ───────────────────────────────────
class AgentUpdateOut(BaseModel):
    """Told to the agent on demand: whether to self-update its .exe, and to what."""
    update: bool = False
    version: str | None = None
    sha256: str | None = None
    size: int = 0


class AgentExeMetaOut(BaseModel):
    """The currently-stored agent exe (for the upload UI)."""
    present: bool = False
    version: str | None = None
    sha256: str | None = None
    size: int = 0
    filename: str | None = None
    uploaded_at: datetime | None = None
    rollout_count: int = 0  # how many stations are opted into the rollout


class ExeRolloutIn(BaseModel):
    agent_ids: list[uuid.UUID] | None = None  # specific stations…
    all: bool = False                          # …or every claimed station
    enabled: bool = True


class ExeRolloutResult(BaseModel):
    updated: int = 0


class ScheduleRolloutIn(BaseModel):
    at: datetime | None = None  # UTC time to fire the full rollout; null = cancel


class ScheduleRolloutOut(BaseModel):
    at: datetime | None = None


class NoticeOut(BaseModel):
    notice: str | None = None
    at: datetime | None = None


class PrinterEventOut(BaseModel):
    """One logged ticket-printer status change (for the card history + report)."""
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str | None = None
    state: str
    prev_state: str | None = None
    detail: str | None = None
    raw: str | None = None
    at: datetime


class AgentSiteIn(BaseModel):
    """Link a station to the UniFi site it should probe (null to unlink)."""
    site_id: uuid.UUID | None = None
