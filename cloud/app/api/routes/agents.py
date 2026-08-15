"""Site agents: registration (token issuance), the push-ingest endpoint the
agents report to, the self-update payload endpoints, and read views."""
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import hash_token, make_agent_token
from app.models import Account, Agent, AgentCommand, Device, PingSample, Site
from app.schemas import (
    AgentCreate,
    AgentOut,
    AgentReport,
    AgentReportResult,
    AgentSiteIn,
    BulkResult,
    BulkStationsIn,
    DeviceProbeReport,
    DeviceProbeResult,
    EnrollAddIn,
    EnrollClaimIn,
    EnrollmentPinOut,
    EnrollResult,
    EnrollStationOut,
    EnrollStationsIn,
    PingPoint,
    ProbeTarget,
    ProbeTargetsOut,
)
from app.core.auth import current_user, require_admin
from app.services.sync import get_or_create_account

router = APIRouter(prefix="/agents", tags=["agents"])


def _gen_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def _get_pin(db: AsyncSession) -> tuple[Account, str]:
    """Return (account, pin), generating a PIN on first use."""
    account = await get_or_create_account(db)
    if not account.enrollment_pin:
        account.enrollment_pin = _gen_pin()
        await db.commit()
        await db.refresh(account)
    return account, account.enrollment_pin


async def _require_pin(db: AsyncSession, pin: str) -> Account:
    account, real = await _get_pin(db)
    if not pin or not secrets.compare_digest(pin.strip(), real):
        raise HTTPException(status_code=401, detail="Wrong enrollment PIN.")
    return account

# The canonical agent payload the bootstrapper downloads and runs. Editing this
# file + bumping PAYLOAD_VERSION rolls the whole fleet out on next check-in.
PAYLOAD_PATH = Path(__file__).resolve().parents[2] / "agent_runtime" / "payload.py"
_VER_RE = re.compile(r"""PAYLOAD_VERSION\s*=\s*["']([^"']+)["']""")


def _payload_source() -> str:
    return PAYLOAD_PATH.read_text(encoding="utf-8")


def _payload_version() -> str:
    m = _VER_RE.search(_payload_source())
    return m.group(1) if m else "0"


async def _agent_from_token(db: AsyncSession, token: str | None) -> Agent:
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Token")
    agent = (
        await db.execute(select(Agent).where(Agent.token_hash == hash_token(token)))
    ).scalars().first()
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return agent


def _client_ip(request: Request) -> str | None:
    """The real client IP behind the Caddy + Docker reverse proxy. request.client
    only sees the proxy hop (172.18.x); Caddy forwards the true remote address in
    X-Forwarded-For (first entry) / X-Real-IP."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


async def _default_site_id(db: AsyncSession) -> uuid.UUID | None:
    """The site stations auto-link to for LAN probing (settings, default 'Main')."""
    name = (get_settings().default_probe_site_name or "").strip()
    if not name:
        return None
    site = (
        await db.execute(select(Site).where(func.lower(Site.name) == name.lower()))
    ).scalars().first()
    return site.id if site else None


async def _ensure_probe_site(db: AsyncSession, agent: Agent) -> bool:
    """Auto-link a station with NO probe site to the default site. Never touches
    a station that was linked (or re-linked) manually. Returns True if changed."""
    if agent.site_id is not None:
        return False
    sid = await _default_site_id(db)
    if sid is None:
        return False
    agent.site_id = sid
    await db.commit()
    return True


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_online(last_seen_at: str | None) -> bool:
    ts = _parse_iso(last_seen_at)
    if ts is None:
        return False
    window = get_settings().agent_offline_after_seconds
    return (datetime.now(tz=timezone.utc) - ts).total_seconds() <= window


def _agent_out(a: Agent, site_name: str | None, latest_rtt: float | None) -> AgentOut:
    online = _is_online(a.last_seen_at)
    return AgentOut(
        id=a.id,
        name=a.name,
        site_id=a.site_id,
        site_name=site_name,
        station_group=a.station_group,
        status=("online" if online else ("offline" if a.last_seen_at else "pending")),
        online=online,
        claimed=bool(a.claimed_at),
        machine_id=a.machine_id,
        version=a.version,
        hostname=a.hostname,
        os=a.os,
        last_ip=a.last_ip,
        last_target=a.last_target,
        last_seen_at=a.last_seen_at,
        latest_rtt_ms=latest_rtt,
    )


# ── registration / management (dashboard side) ───────────────────────────────
@router.post("", response_model=AgentOut)
async def create_agent(
    payload: AgentCreate,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> AgentOut:
    """Create a *station* — a named slot a kiosk claims on first run. No token is
    issued here; claiming (via the enrollment PIN) mints the token on the kiosk."""
    account = await get_or_create_account(db)
    if payload.site_id is not None and await db.get(Site, payload.site_id) is None:
        raise HTTPException(status_code=400, detail="Unknown site_id")
    if payload.station_group not in ("kiosk", "ticketbox"):
        raise HTTPException(status_code=422, detail="station_group must be kiosk or ticketbox")
    agent = Agent(
        account_id=account.id,
        site_id=payload.site_id,
        name=payload.name,
        station_group=payload.station_group,
        token_hash="",  # unclaimed until a kiosk enrolls
        status="pending",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    site_name = None
    if agent.site_id:
        s = await db.get(Site, agent.site_id)
        site_name = s.name if s else None
    return _agent_out(agent, site_name, None)


@router.post("/bulk", response_model=BulkResult)
async def bulk_create_stations(
    body: BulkStationsIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> BulkResult:
    """Create many stations at once (duplicates by name are skipped)."""
    account = await get_or_create_account(db)
    existing = {a.name for a in (await db.execute(select(Agent))).scalars()}
    created = skipped = 0
    for raw in body.names:
        name = (raw or "").strip()
        if not name:
            continue
        if name in existing:
            skipped += 1
            continue
        db.add(Agent(account_id=account.id, name=name, token_hash="", status="pending",
                     station_group=body.station_group if body.station_group in ("kiosk", "ticketbox") else "kiosk"))
        existing.add(name)
        created += 1
    await db.commit()
    return BulkResult(created=created, skipped=skipped)


@router.post("/{agent_id}/release", response_model=AgentOut)
async def release_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> AgentOut:
    """Un-claim a station so a (different) kiosk can enroll as it again."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Station not found")
    agent.claimed_at = None
    agent.machine_id = None
    agent.token_hash = ""
    await db.commit()
    await db.refresh(agent)
    site_name = None
    if agent.site_id:
        s = await db.get(Site, agent.site_id)
        site_name = s.name if s else None
    return _agent_out(agent, site_name, None)


class AgentGroupIn(BaseModel):
    station_group: str


@router.post("/{agent_id}/group", response_model=AgentOut)
async def set_agent_group(
    agent_id: uuid.UUID,
    body: AgentGroupIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> AgentOut:
    """Move a station between dashboard tabs (kiosk | ticketbox)."""
    if body.station_group not in ("kiosk", "ticketbox"):
        raise HTTPException(status_code=422, detail="station_group must be kiosk or ticketbox")
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Station not found")
    agent.station_group = body.station_group
    await db.commit()
    await db.refresh(agent)
    site_name = None
    if agent.site_id:
        s = await db.get(Site, agent.site_id)
        site_name = s.name if s else None
    return _agent_out(agent, site_name, None)


@router.post("/{agent_id}/site", response_model=AgentOut)
async def set_agent_site(
    agent_id: uuid.UUID,
    body: AgentSiteIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> AgentOut:
    """Link a station to the UniFi site it should probe on its LAN (or null to
    unlink). This is what tells the agent which devices to ping."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Station not found")
    site_name = None
    if body.site_id is not None:
        site = await db.get(Site, body.site_id)
        if site is None:
            raise HTTPException(status_code=400, detail="Unknown site_id")
        site_name = site.name
    agent.site_id = body.site_id
    await db.commit()
    await db.refresh(agent)
    return _agent_out(agent, site_name, None)


@router.get("", response_model=list[AgentOut])
async def list_agents(db: AsyncSession = Depends(get_db), _user=Depends(current_user)) -> list[AgentOut]:
    agents = (await db.execute(select(Agent).order_by(Agent.name))).scalars().all()
    sites = {s.id: s.name for s in (await db.execute(select(Site))).scalars()}
    out: list[AgentOut] = []
    for a in agents:
        latest = (
            await db.execute(
                select(PingSample.rtt_ms)
                .where(PingSample.agent_id == a.id)
                .order_by(desc(PingSample.ts))
                .limit(1)
            )
        ).scalars().first()
        out.append(_agent_out(a, sites.get(a.site_id) if a.site_id else None, latest))
    return out


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> None:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        return
    await db.execute(PingSample.__table__.delete().where(PingSample.agent_id == agent_id))
    await db.delete(agent)
    await db.commit()


@router.get("/{agent_id}/pings", response_model=list[PingPoint])
async def agent_pings(
    agent_id: uuid.UUID,
    limit: int = Query(300, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[PingPoint]:
    if await db.get(Agent, agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    rows = list(
        (
            await db.execute(
                select(PingSample)
                .where(PingSample.agent_id == agent_id)
                .order_by(desc(PingSample.ts))
                .limit(limit)
            )
        ).scalars()
    )
    rows.reverse()  # chronological for charting
    return [PingPoint(ts=s.ts, rtt_ms=s.rtt_ms, gateway_rtt_ms=s.gateway_rtt_ms) for s in rows]


@router.get("/{agent_id}/pings/summary")
async def agent_ping_summary(
    agent_id: uuid.UUID,
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> dict:
    """Aggregate a window of ping samples (default 24h) for the PDF report.

    Buckets by minute (avg/max latency + loss per minute) so a full day charts
    from ~1440 points instead of ~86k raw rows, plus whole-window summary stats.
    Aggregation runs in SQL — never pulls the raw samples into the app.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=hours)
    window = and_(PingSample.agent_id == agent_id, PingSample.ts >= since)

    # Per-minute buckets for the chart.
    bucket = func.date_trunc("minute", PingSample.ts).label("bucket")
    brows = (
        await db.execute(
            select(
                bucket,
                func.count().label("n"),
                func.count(PingSample.rtt_ms).label("ok"),
                func.avg(PingSample.rtt_ms).label("avg_rtt"),
                func.max(PingSample.rtt_ms).label("max_rtt"),
            )
            .where(window)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()

    buckets = []
    for r in brows:
        n = int(r.n or 0)
        ok = int(r.ok or 0)
        bts = r.bucket
        buckets.append(
            {
                "ts": bts.isoformat() if hasattr(bts, "isoformat") else str(bts),
                "avg_rtt_ms": round(float(r.avg_rtt), 2) if r.avg_rtt is not None else None,
                "max_rtt_ms": round(float(r.max_rtt), 2) if r.max_rtt is not None else None,
                "loss_pct": round((n - ok) / n * 100, 1) if n else 0.0,
                "n": n,
            }
        )

    # Whole-window summary stats.
    s = (
        await db.execute(
            select(
                func.count().label("n"),
                func.count(PingSample.rtt_ms).label("ok"),
                func.avg(PingSample.rtt_ms).label("avg_rtt"),
                func.min(PingSample.rtt_ms).label("min_rtt"),
                func.max(PingSample.rtt_ms).label("max_rtt"),
                func.avg(PingSample.gateway_rtt_ms).label("avg_gw"),
                func.min(PingSample.ts).label("first_ts"),
                func.max(PingSample.ts).label("last_ts"),
            ).where(window)
        )
    ).one()
    n = int(s.n or 0)
    ok = int(s.ok or 0)

    # p95 (Postgres percentile_cont); degrade gracefully if the DB lacks it.
    p95 = None
    try:
        p95 = (
            await db.execute(
                select(
                    func.percentile_cont(0.95).within_group(PingSample.rtt_ms.asc())
                ).where(and_(window, PingSample.rtt_ms.isnot(None)))
            )
        ).scalar()
        p95 = round(float(p95), 2) if p95 is not None else None
    except Exception:
        p95 = None

    def _f(v):
        return round(float(v), 2) if v is not None else None

    return {
        "hours": hours,
        "generated_at": now.isoformat(),
        "target": agent.last_target,
        "first_ts": s.first_ts.isoformat() if s.first_ts is not None else None,
        "last_ts": s.last_ts.isoformat() if s.last_ts is not None else None,
        "stats": {
            "samples": n,
            "loss_pct": round((n - ok) / n * 100, 2) if n else 0.0,
            "uptime_pct": round(ok / n * 100, 2) if n else 0.0,
            "avg_rtt_ms": _f(s.avg_rtt),
            "min_rtt_ms": _f(s.min_rtt),
            "max_rtt_ms": _f(s.max_rtt),
            "p95_rtt_ms": p95,
            "avg_gateway_rtt_ms": _f(s.avg_gw),
        },
        "buckets": buckets,
    }


@router.get("/pings/recent")
async def agents_recent_pings(
    minutes: int = Query(45, ge=5, le=240),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> dict:
    """Per-minute average latency for EVERY agent in one call — feeds the
    always-on card sparklines without one request per kiosk. Aggregates in SQL."""
    since = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    bucket = func.date_trunc("minute", PingSample.ts).label("bucket")
    rows = (
        await db.execute(
            select(
                PingSample.agent_id,
                bucket,
                func.avg(PingSample.rtt_ms).label("avg_rtt"),
                func.count().label("n"),
                func.count(PingSample.rtt_ms).label("ok"),
            )
            .where(PingSample.ts >= since)
            .group_by(PingSample.agent_id, bucket)
            .order_by(PingSample.agent_id, bucket)
        )
    ).all()
    out: dict[str, list] = {}
    for r in rows:
        n = int(r.n or 0)
        ok = int(r.ok or 0)
        out.setdefault(str(r.agent_id), []).append(
            {
                "ts": r.bucket.isoformat() if hasattr(r.bucket, "isoformat") else str(r.bucket),
                "rtt": round(float(r.avg_rtt), 1) if r.avg_rtt is not None else None,
                "loss": bool(n and ok < n),
            }
        )
    return out


# ── ingest (agent side) ──────────────────────────────────────────────────────
@router.post("/report", response_model=AgentReportResult)
async def agent_report(
    report: AgentReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> AgentReportResult:
    agent = await _agent_from_token(db, x_agent_token)

    now = datetime.now(tz=timezone.utc)
    agent.last_seen_at = now.isoformat()
    agent.status = "online"
    if report.agent_version:
        agent.version = report.agent_version
    if report.hostname:
        agent.hostname = report.hostname
    if report.os:
        agent.os = report.os
    if report.target:
        agent.last_target = report.target
    client_ip = _client_ip(request)
    if client_ip:
        agent.last_ip = client_ip

    stored = 0
    for s in report.samples:
        ts = datetime.fromtimestamp(s.ts, tz=timezone.utc) if s.ts else now
        db.add(
            PingSample(
                agent_id=agent.id,
                ts=ts,
                target=report.target,
                rtt_ms=s.rtt,
                gateway_rtt_ms=s.gw,
            )
        )
        stored += 1
    # Deliver queued commands (Phase 3): mark them "sent" so each is delivered
    # exactly once; the agent answers on /agents/command-result.
    pending = list(
        (await db.execute(
            select(AgentCommand)
            .where(AgentCommand.agent_id == agent.id, AgentCommand.status == "queued")
            .order_by(AgentCommand.created_at)
            .limit(5)
        )).scalars()
    )
    commands = []
    for c in pending:
        # SAFETY DRAIN: the device-I/O commands can crash the agent process on
        # some kiosks (a native access violation Python can't catch). Until the
        # payload runs that I/O crash-isolated, never hand these to a kiosk —
        # cancel them server-side so a queued backlog can't crash-loop the agent.
        if c.kind in _CRASH_ISOLATED_KINDS:
            c.status = "error"
            c.result = {"error": "temporarily disabled — crash-safe printer I/O in progress"}
            c.completed_at = now
            continue
        c.status, c.sent_at = "sent", now
        commands.append({"id": str(c.id), "kind": c.kind, "args": c.args or {}})
    await db.commit()
    return AgentReportResult(ok=True, stored=stored, commands=commands)


# ── local LAN probing (agent pings the site's UniFi devices) ─────────────────
@router.get("/targets", response_model=ProbeTargetsOut)
async def agent_targets(
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> ProbeTargetsOut:
    """Devices the agent should ping on its local LAN — the UniFi devices of the
    site this station is linked to that currently have an IP. Empty until the
    station is linked to a site (dashboard → Manage stations → Probe site)."""
    agent = await _agent_from_token(db, x_agent_token)
    # Kiosks all live at Main — a station nobody linked yet links itself here on
    # its first sweep (manual per-station overrides are respected).
    await _ensure_probe_site(db, agent)
    if agent.site_id is None:
        return ProbeTargetsOut(site_id=None, site_name=None, targets=[])
    site = await db.get(Site, agent.site_id)
    rows = (
        await db.execute(
            select(Device).where(
                Device.site_id == agent.site_id, Device.ip.is_not(None)
            )
        )
    ).scalars()
    targets = [
        ProbeTarget(id=d.id, name=d.name, ip=d.ip, mac=d.mac)
        for d in rows
        if d.ip
    ]
    return ProbeTargetsOut(
        site_id=agent.site_id,
        site_name=site.name if site else None,
        interval=get_settings().agent_probe_interval_seconds,
        targets=targets,
    )


@router.post("/device-report", response_model=DeviceProbeResult)
async def agent_device_report(
    report: DeviceProbeReport,
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> DeviceProbeResult:
    """Ingest local reachability results from an on-site agent. Updates each
    device's local_reachable/local_rtt_ms/local_checked_at (scoped to the agent's
    linked site so an agent can only touch its own site's devices).

    Multi-vantage merge: with many kiosks probing the same site, a device is
    reachable if ANY kiosk reached it — a "reachable" sighting protects the
    device from other kiosks' "unreachable" reports for a grace window, so
    status doesn't flicker when one far-corner kiosk misses a ping."""
    agent = await _agent_from_token(db, x_agent_token)
    await _ensure_probe_site(db, agent)
    if agent.site_id is None:
        return DeviceProbeResult(ok=True, updated=0)
    now = datetime.now(tz=timezone.utc)
    grace = get_settings().probe_positive_grace_seconds
    updated = 0
    for r in report.results:
        dev = await db.get(Device, r.id)
        if dev is None or dev.site_id != agent.site_id:
            continue
        if r.reachable:
            dev.local_reachable = True
            dev.local_rtt_ms = r.rtt_ms
            dev.local_checked_at = now
        else:
            recently_seen_up = (
                dev.local_reachable is True
                and dev.local_checked_at is not None
                and (now - dev.local_checked_at).total_seconds() < grace
            )
            if not recently_seen_up:
                dev.local_reachable = False
                dev.local_rtt_ms = None
                dev.local_checked_at = now
        updated += 1
    await db.commit()
    return DeviceProbeResult(ok=True, updated=updated)


# ── self-update: agents fetch the latest payload from here ───────────────────
@router.get("/version")
async def agent_payload_version(
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> dict:
    """Cheap version check — the agent polls this and only downloads a new
    payload when the version differs from what it's running."""
    await _agent_from_token(db, x_agent_token)
    return {"version": _payload_version()}


@router.get("/payload", response_class=PlainTextResponse)
async def agent_payload(
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> PlainTextResponse:
    """The current agent payload source. The bootstrapper caches and runs it.
    Version is also returned in the X-Payload-Version header."""
    await _agent_from_token(db, x_agent_token)
    return PlainTextResponse(
        _payload_source(),
        headers={"X-Payload-Version": _payload_version()},
    )


# ── enrollment PIN management (dashboard side — behind basic-auth) ────────────
@router.get("/enrollment", response_model=EnrollmentPinOut)
async def get_enrollment_pin(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> EnrollmentPinOut:
    _, pin = await _get_pin(db)
    return EnrollmentPinOut(pin=pin)


@router.post("/enrollment/regenerate", response_model=EnrollmentPinOut)
async def regenerate_enrollment_pin(
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> EnrollmentPinOut:
    account = await get_or_create_account(db)
    account.enrollment_pin = _gen_pin()
    await db.commit()
    await db.refresh(account)
    return EnrollmentPinOut(pin=account.enrollment_pin)


# ── enrollment (kiosk first-run station picker — PIN-gated, no token yet) ─────
async def _claim(agent: Agent, hostname: str | None, machine_id: str | None) -> str:
    """Mint a fresh token for this station and bind it to the claiming machine."""
    token = make_agent_token()
    agent.token_hash = hash_token(token)
    agent.claimed_at = datetime.now(tz=timezone.utc).isoformat()
    agent.machine_id = machine_id
    if hostname:
        agent.hostname = hostname
    return token


@router.post("/enroll/stations", response_model=list[EnrollStationOut])
async def enroll_stations(body: EnrollStationsIn, db: AsyncSession = Depends(get_db)) -> list[EnrollStationOut]:
    await _require_pin(db, body.pin)
    agents = (await db.execute(select(Agent).order_by(Agent.name))).scalars().all()
    return [EnrollStationOut(id=a.id, name=a.name, claimed=bool(a.claimed_at)) for a in agents]


@router.post("/enroll/claim", response_model=EnrollResult)
async def enroll_claim(body: EnrollClaimIn, db: AsyncSession = Depends(get_db)) -> EnrollResult:
    await _require_pin(db, body.pin)
    agent = await db.get(Agent, body.station_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Station not found")
    # A station already bound to a DIFFERENT machine must be released first.
    if agent.claimed_at and agent.machine_id and body.machine_id and agent.machine_id != body.machine_id:
        raise HTTPException(
            status_code=409,
            detail=f"'{agent.name}' is already set up on another machine. "
                   "Release it in the dashboard, or pick a different station.",
        )
    token = await _claim(agent, body.hostname, body.machine_id)
    if agent.site_id is None:
        agent.site_id = await _default_site_id(db)  # kiosks live at Main
    await db.commit()
    return EnrollResult(token=token, name=agent.name)


@router.post("/enroll/add", response_model=EnrollResult)
async def enroll_add(body: EnrollAddIn, db: AsyncSession = Depends(get_db)) -> EnrollResult:
    account = await _require_pin(db, body.pin)
    agent = Agent(account_id=account.id, name=body.name, token_hash="", status="pending")
    token = await _claim(agent, body.hostname, body.machine_id)
    agent.site_id = await _default_site_id(db)  # kiosks live at Main
    db.add(agent)
    await db.commit()
    return EnrollResult(token=token, name=agent.name)


# ── Live landing-page probes (designated kiosk only) ─────────────────────────
# The payload asks every ~60s whether IT is the designated Live probe. Only the
# designated kiosk gets a target list; everyone else gets enabled=false and
# changes nothing about its behavior.

@router.get("/live-config")
async def live_config(
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> dict:
    from app.api.routes.live import ensure_default_targets

    from app.models import ProbeTarget

    agent = await _agent_from_token(db, x_agent_token)
    account = await ensure_default_targets(db)  # seeds the classic set once
    st = get_settings()
    if account.probe_agent_id != agent.id:
        return {"enabled": False}
    targets = (
        await db.execute(
            select(ProbeTarget)
            .where(ProbeTarget.account_id == account.id, ProbeTarget.enabled.is_(True))
            .order_by(ProbeTarget.sort)
        )
    ).scalars()
    return {
        "enabled": True,
        "targets": [
            {"id": str(t.id), "kind": t.kind, "target": t.target} for t in targets
        ],
        "ping_interval": st.live_agent_ping_interval_seconds,
        "http_interval": st.live_agent_http_interval_seconds,
        "post_interval": st.live_agent_post_interval_seconds,
    }


@router.post("/probe-report")
async def probe_report(
    body: dict,
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> dict:
    """Bulk-ingest the designated kiosk's probe samples.
    Body: {"samples": [{"target_id": "...", "ts": epoch_s, "ms": 1.2|null}, …]}"""
    from app.models import ProbeSample, ProbeTarget

    agent = await _agent_from_token(db, x_agent_token)
    samples = (body or {}).get("samples") or []
    if not isinstance(samples, list):
        raise HTTPException(status_code=422, detail="samples must be a list")
    samples = samples[:2000]
    # Only accept samples for THIS account's real targets.
    valid_ids = {
        str(t.id)
        for t in (
            await db.execute(
                select(ProbeTarget).where(ProbeTarget.account_id == agent.account_id)
            )
        ).scalars()
    }
    accepted = 0
    for s in samples:
        try:
            tid = str(s["target_id"])
            if tid not in valid_ids:
                continue
            ts = datetime.fromtimestamp(float(s["ts"]), tz=timezone.utc)
            ms = s.get("ms")
            ms = float(ms) if ms is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        db.add(ProbeSample(target_id=uuid.UUID(tid), agent_id=agent.id, ts=ts, ms=ms))
        accepted += 1
    await db.commit()
    return {"accepted": accepted}


# ── Remote commands (Phase 3): allow-listed, audited, queue = audit log ──────
# Growing this list is a deliberate act: each kind needs agent-side handling in
# the payload AND a reason to exist. Never a free-form shell.
# printer-status = safe (WMI, no ctypes). printer-probe/printer-raw do native
# device I/O and are temporarily gated off until the payload isolates them in a
# child process so a crash can't take the agent down.
ALLOWED_COMMAND_KINDS = {"printer-status"}
_CRASH_ISOLATED_KINDS = {"printer-probe", "printer-raw"}


class CommandIn(BaseModel):
    kind: str
    args: dict | None = None


class CommandOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    kind: str
    args: dict | None = None
    status: str
    requested_by: str
    result: dict | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None
    completed_at: datetime | None = None


def _command_out(c: AgentCommand) -> CommandOut:
    return CommandOut(
        id=c.id, agent_id=c.agent_id, kind=c.kind, args=c.args, status=c.status,
        requested_by=c.requested_by, result=c.result, created_at=c.created_at,
        sent_at=c.sent_at, completed_at=c.completed_at,
    )


@router.post("/{agent_id}/commands", response_model=CommandOut)
async def queue_command(
    agent_id: uuid.UUID,
    body: CommandIn,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
) -> CommandOut:
    if body.kind not in ALLOWED_COMMAND_KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown command kind '{body.kind}'")
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Station not found")
    cmd = AgentCommand(agent_id=agent.id, kind=body.kind, args=body.args,
                       status="queued", requested_by=getattr(admin, "email", ""))
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    return _command_out(cmd)


@router.get("/{agent_id}/commands", response_model=list[CommandOut])
async def list_commands(
    agent_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[CommandOut]:
    rows = (
        await db.execute(
            select(AgentCommand)
            .where(AgentCommand.agent_id == agent_id)
            .order_by(desc(AgentCommand.created_at))
            .limit(limit)
        )
    ).scalars()
    return [_command_out(c) for c in rows]


@router.post("/command-result")
async def command_result(
    body: dict,
    db: AsyncSession = Depends(get_db),
    x_agent_token: str | None = Header(default=None),
) -> dict:
    """Agent answers a delivered command: {"id": ..., "ok": bool, "result": {...}}."""
    agent = await _agent_from_token(db, x_agent_token)
    try:
        cmd_id = uuid.UUID(str(body.get("id")))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Bad command id")
    cmd = await db.get(AgentCommand, cmd_id)
    if cmd is None or cmd.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Command not found")
    result = body.get("result")
    cmd.result = result if isinstance(result, dict) else {"raw": result}
    cmd.status = "done" if body.get("ok") else "error"
    cmd.completed_at = datetime.now(tz=timezone.utc)
    await db.commit()
    return {"ok": True}
