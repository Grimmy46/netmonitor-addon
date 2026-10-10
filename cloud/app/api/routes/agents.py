"""Site agents: registration (token issuance), the push-ingest endpoint the
agents report to, the self-update payload endpoints, and read views."""
import hashlib
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import hash_token, make_agent_token
from app.models import (
    Account, Agent, AgentBinary, AgentCommand, Device, FleetBatch, PingSample, PrinterEvent, Site,
)
from app.schemas import (
    AgentCreate,
    AgentExeMetaOut,
    AgentOut,
    AgentReport,
    AgentReportResult,
    AgentSiteIn,
    AgentUpdateOut,
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
    ExeRolloutIn,
    ExeRolloutResult,
    NoticeOut,
    PingPoint,
    PrinterEventOut,
    ProbeTarget,
    ScheduleRolloutIn,
    ScheduleRolloutOut,
    ProbeTargetsOut,
    TeardownIn,
    TeardownStatusOut,
)
from app.core.auth import current_user, require_admin
from app.services.sync import get_or_create_account

logger = logging.getLogger("netmonitor.agents")

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


def _paper_fields(a: Agent) -> dict:
    """Current-roll gauge for the dashboard (printed-cm based when available)."""
    from app.services import paper as _paper
    u = _paper.roll_usage(a)
    if u is None:
        return {}
    return {
        "printer_cut_count": a.printer_cut_count,
        "printer_roll_percent": min(100.0, round(100.0 * u["frac"], 1)),
        "printer_cuts_remaining": u["tickets_left"],
        "printer_cuts_per_roll": (round(float(a.printer_cuts_per_roll or 0), 1) or None),
        "printer_roll_learned": u["learned"],
        "printer_roll_partial": u["partial"],
        "printer_paper_cm": a.printer_paper_cm,
        "printer_roll_used_cm": u["used_cm"],
        "printer_roll_cm": round(u["roll_cm"]) if u["roll_cm"] else None,
        "printer_tickets_this_roll": u["tickets_this_roll"],
        "printer_near_end": a.printer_near_end,
    }


def _agent_out(a: Agent, site_name: str | None, latest_rtt: float | None) -> AgentOut:
    online = _is_online(a.last_seen_at)
    return AgentOut(
        **_paper_fields(a),
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
        bootstrap_version=a.bootstrap_version,
        exe_rollout=a.exe_rollout,
        printer_status=a.printer_status,
        printer_status_at=a.printer_status_at,
        printer_detail=a.printer_detail,
        printer_raw=a.printer_raw,
        lan_ip=a.lan_ip,
        switch_name=a.switch_name,
        switch_port=a.switch_port,
        switch_mac=a.switch_mac,
        powered_off_at=a.powered_off_at.isoformat() if a.powered_off_at else None,
        stale=_is_stale(a.last_seen_at),
    )


def _is_stale(last_seen_at: str | None, days: int = 7) -> bool:
    ts = _parse_iso(last_seen_at)
    return ts is not None and (datetime.now(tz=timezone.utc) - ts).days >= days


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
    agent.powered_off_at = None
    # A ticket-box install kit tags its PC: applied on the agent's FIRST report
    # (a fresh install), so an admin's later regroup in the dashboard sticks.
    if report.station_group in ("kiosk", "ticketbox") and agent.version is None:
        agent.station_group = report.station_group
    if report.agent_version:
        agent.version = report.agent_version
    if report.bootstrap_version:
        agent.bootstrap_version = report.bootstrap_version
    if report.hostname:
        agent.hostname = report.hostname
    if report.os:
        agent.os = report.os
    if report.target:
        agent.last_target = report.target
    if report.printer is not None:
        p = report.printer
        # A printer that's present reports a state; when absent we clear the
        # stored status so a removed/unplugged printer stops showing/alerting.
        prev = agent.printer_status
        new_state = (p.state or "unknown") if p.present else None
        agent.printer_status = new_state
        agent.printer_status_at = now.isoformat()
        agent.printer_raw = p.raw if p.present else None
        agent.printer_detail = p.detail if p.present else None
        # Log only transitions — the table is an event history, not a per-poll log.
        if new_state != prev:
            db.add(PrinterEvent(
                account_id=agent.account_id, agent_id=agent.id,
                state=new_state or "removed", prev_state=prev,
                raw=(p.raw if p.present else None),
                detail=(p.detail if p.present else "printer disconnected"),
            ))
        # Predictive paper: fold in the lifetime cut count (uses prev/new_state
        # to detect run-outs and reloads).
        if p.present:
            from app.services import paper as _paper
            await _paper.track(db, agent, p.cut_count, p.paper_cm, p.near_end, prev, new_state, now)
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
    supports_worker = _supports_crash_isolation(agent.bootstrap_version)
    commands = []
    for c in pending:
        # Device-I/O commands can crash the agent process (a native access
        # violation Python can't catch), so they run in a crash-isolated worker
        # subprocess — only exes that support it (bootstrap ≥ 2.5) may receive
        # them. Anything queued for an older exe is cancelled, never delivered,
        # so a backlog can't crash-loop the agent.
        if c.kind in _CRASH_ISOLATED_KINDS and not supports_worker:
            c.status = "error"
            c.result = {"error": "this kiosk's agent .exe is too old for isolated printer I/O "
                                 "(needs bootstrap ≥ 2.5) — update the exe to use it"}
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
    devs = [d for d in rows if d.ip]
    # Spread the work: each device goes to K online kiosks at this site
    # (app/services/pingwatch.py), so every device is pinged every few seconds
    # from several vantage points without every kiosk pinging everything.
    from app.services import pingwatch
    peers = (await db.execute(select(Agent.id).where(
        Agent.site_id == agent.site_id, Agent.status == "online"))).scalars().all()
    mine = pingwatch.assign([d.id for d in devs], peers, agent.id)
    targets = [ProbeTarget(id=d.id, name=d.name, ip=d.ip, mac=d.mac)
               for d in devs if str(d.id) in mine]
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
    updated = 0
    # Lock every device row this report touches in ONE query, in primary-key
    # order. ~50 kiosks at a site report on the same rows concurrently; locking
    # them one by one in arrival order made two reports grab rows in opposite
    # orders and deadlock (hundreds of 500s a day). A single ordered
    # SELECT ... FOR UPDATE gives every transaction the same lock order.
    ids = sorted({r.id for r in report.results}, key=str)
    devices: dict = {}
    if ids:
        rows = (
            await db.execute(
                select(Device)
                .where(Device.id.in_(ids), Device.site_id == agent.site_id)
                .order_by(Device.id)
                .with_for_update()
            )
        ).scalars()
        devices = {d.id: d for d in rows}
    # Kiosk pings decide up/down (pingwatch): any reply = up; 2+ healthy
    # kiosks failing with no reply for 40 s = down — minutes ahead of UniFi.
    from app.services import pingwatch
    from app.services.sync import _apply_online_state
    agent_ok = any(r.reachable for r in report.results)
    went_down, came_up = [], []
    for r in report.results:
        dev = devices.get(r.id)
        if dev is None:
            continue
        change = pingwatch.record(dev.id, agent.id, r.reachable, agent_ok, now)
        if r.reachable:
            dev.local_reachable = True
            dev.local_rtt_ms = r.rtt_ms
            dev.local_checked_at = now
        elif change == "down" or pingwatch.verdict(dev.id, now) is False:
            dev.local_reachable = False
            dev.local_rtt_ms = None
            dev.local_checked_at = now
        if change == "down" and dev.is_online is not False:
            _apply_online_state(dev, False, pingwatch.first_fail(dev.id) or now)
            went_down.append(dev)
        elif change == "up" and dev.is_online is False:
            _apply_online_state(dev, True, now)
            came_up.append(dev)
        updated += 1
    await db.commit()
    if went_down or came_up:
        logger.info("Ping verdict: down %s, up %s", [d.name for d in went_down], [d.name for d in came_up])
        try:
            from app.services import fastalert
            site = await db.get(Site, agent.site_id)
            if site is not None:
                await fastalert.notify(db, site, went_down, came_up, [], [], now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fast alert (ping) failed: %s", exc)
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


# ── Agent exe self-update (staged rollout) ───────────────────────────────────
async def _active_binary(db: AsyncSession) -> AgentBinary | None:
    return (await db.execute(
        select(AgentBinary).where(AgentBinary.active.is_(True))
        .order_by(desc(AgentBinary.created_at)).limit(1)
    )).scalar_one_or_none()


async def _rollout_count(db: AsyncSession) -> int:
    return int((await db.execute(
        select(func.count()).select_from(Agent).where(Agent.exe_rollout.is_(True))
    )).scalar_one())


@router.post("/agent-exe", response_model=AgentExeMetaOut)
async def upload_agent_exe(
    file: UploadFile = File(...),
    version: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> AgentExeMetaOut:
    """Admin uploads the current NetMonAgent.exe; kiosks opted into the rollout
    download it, verify the sha256, and swap. Replaces any previous binary."""
    st = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > st.agent_exe_max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"exe too large (> {st.agent_exe_max_mb} MB)")
    ver = version.strip()
    if not ver:
        raise HTTPException(status_code=400, detail="version required")
    sha = hashlib.sha256(data).hexdigest()
    account = await get_or_create_account(db)
    await db.execute(delete(AgentBinary))  # single active binary
    row = AgentBinary(account_id=account.id, version=ver, sha256=sha, size=len(data),
                      filename=(file.filename or "NetMonAgent.exe"), data=data, active=True)
    db.add(row)
    await db.commit(); await db.refresh(row)
    return AgentExeMetaOut(present=True, version=row.version, sha256=row.sha256, size=row.size,
                           filename=row.filename, uploaded_at=row.created_at,
                           rollout_count=await _rollout_count(db))


@router.get("/agent-exe", response_model=AgentExeMetaOut)
async def get_agent_exe_meta(
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> AgentExeMetaOut:
    row = await _active_binary(db)
    count = await _rollout_count(db)
    if row is None:
        return AgentExeMetaOut(present=False, rollout_count=count)
    return AgentExeMetaOut(present=True, version=row.version, sha256=row.sha256, size=row.size,
                           filename=row.filename, uploaded_at=row.created_at, rollout_count=count)


@router.get("/agent-exe/download")
async def download_agent_exe(
    db: AsyncSession = Depends(get_db), x_agent_token: str | None = Header(default=None),
):
    """Agent-token download of the active exe (integrity is the agent's job — it
    verifies X-Exe-Sha256 before swapping)."""
    await _agent_from_token(db, x_agent_token)
    row = await _active_binary(db)
    if row is None:
        raise HTTPException(status_code=404, detail="no agent exe uploaded")
    return Response(content=row.data, media_type="application/octet-stream",
                    headers={"X-Exe-Sha256": row.sha256, "X-Exe-Version": row.version,
                             "Content-Disposition": f'attachment; filename="{row.filename}"'})


@router.get("/install-kit")
async def download_install_kit(
    group: str = Query("kiosk", pattern="^(kiosk|ticketbox)$"),
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
):
    """Admin download: a zip with everything a NEW kiosk needs — the active exe,
    the current payload, a token-less config and install.bat. The kiosk picks
    its station (PIN + dropdown) on first run."""
    import io, json as _json, zipfile
    row = await _active_binary(db)
    if row is None:
        raise HTTPException(status_code=404, detail="No agent exe uploaded yet (Kiosks → Agent update)")
    st = get_settings()
    cfg = {"server_url": str(getattr(st, "public_base_url", "") or "https://rcs-fleet-mon.duckdns.org").rstrip("/"),
           "token": "", "enroll_pin": "", "enroll_auto": True, "target": "rcs.funcardapp.com",
           "gateway": "auto", "interval": 1.0, "post_interval": 30.0, "timeout": 2.0,
           "max_buffer": 5000, "version_check_interval": 600, "autostart": True,
           "station_group": group}
    tb = group == "ticketbox"
    noun = "ticket box" if tb else "kiosk"
    prefix = "TB-" if tb else ""
    tab = "Ticket Boxes" if tb else "Kiosks"
    bat = (
        f"@echo off\r\nrem NetMonitor {noun} agent - one-shot installer\r\nset DEST=C:\\NetMonAgent\r\n"
        "if not exist \"%DEST%\" mkdir \"%DEST%\"\r\n"
        "copy /Y \"%~dp0NetMonAgent.exe\" \"%DEST%\" >nul\r\n"
        "copy /Y \"%~dp0agent_payload.py\" \"%DEST%\" >nul\r\n"
        "if not exist \"%DEST%\\netmon_agent.config.json\" copy /Y \"%~dp0netmon_agent.config.json\" \"%DEST%\" >nul\r\n"
        "if not exist \"%DEST%\\NetMonAgent.exe\" ( echo Copy failed. & pause & exit /b 1 )\r\n"
        "start \"\" \"%DEST%\\NetMonAgent.exe\"\r\n"
        "echo Installed to %DEST%. Enter the PIN and pick the station in the setup window.\r\n"
        "timeout /t 8 >nul\r\n"
    )
    readme = (
        f"NetMonitor {noun} agent - install kit\r\n\r\n"
        f"1. Copy this folder to the {noun} PC (USB stick is fine).\r\n"
        "2. Double-click install.bat.\r\n"
        "3. In the setup window type the enrollment PIN (Settings > Kiosks & stations),\r\n"
        "   pick the station or 'Add a new station', click Save & start.\r\n"
        f"The PC shows up on the {tab} tab within a minute and updates itself after that.\r\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("NetMonAgent/NetMonAgent.exe", row.data)
        z.writestr("NetMonAgent/agent_payload.py", _payload_source())
        z.writestr("NetMonAgent/netmon_agent.config.json", _json.dumps(cfg, indent=2))
        z.writestr("NetMonAgent/install.bat", bat)
        z.writestr("NetMonAgent/README.txt", readme)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="NetMonAgent-{prefix}kit-{row.version}.zip"'})


@router.get("/agent-update", response_model=AgentUpdateOut)
async def agent_update_descriptor(
    db: AsyncSession = Depends(get_db), x_agent_token: str | None = Header(default=None),
) -> AgentUpdateOut:
    """The agent asks whether to self-update its exe. Yes only when THIS station is
    opted into the rollout AND the stored exe differs from what it's running."""
    agent = await _agent_from_token(db, x_agent_token)
    row = await _active_binary(db)
    if row is None or not agent.exe_rollout:
        return AgentUpdateOut(update=False)
    needs = (agent.bootstrap_version or "") != row.version
    return AgentUpdateOut(update=needs, version=row.version, sha256=row.sha256, size=row.size)


@router.post("/exe-rollout", response_model=ExeRolloutResult)
async def set_exe_rollout(
    body: ExeRolloutIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> ExeRolloutResult:
    """Opt stations into (or out of) the exe self-update. Staged: flip a couple,
    watch a nightly power-cycle, then flip all."""
    stmt = select(Agent)
    if not body.all:
        ids = body.agent_ids or []
        if not ids:
            return ExeRolloutResult(updated=0)
        stmt = stmt.where(Agent.id.in_(ids))
    agents = list((await db.execute(stmt)).scalars())
    for a in agents:
        a.exe_rollout = body.enabled
    await db.commit()
    return ExeRolloutResult(updated=len(agents))


@router.post("/exe-rollout/schedule", response_model=ScheduleRolloutOut)
async def schedule_exe_rollout(
    body: ScheduleRolloutIn,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> ScheduleRolloutOut:
    """Arm (or cancel, with at=null) a full fleet rollout for a future UTC time.
    The alert sweep flags every claimed station once that time passes."""
    account = await get_or_create_account(db)
    account.exe_rollout_at = body.at
    await db.commit()
    return ScheduleRolloutOut(at=account.exe_rollout_at)


@router.get("/exe-rollout/schedule", response_model=ScheduleRolloutOut)
async def get_scheduled_rollout(
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> ScheduleRolloutOut:
    account = await get_or_create_account(db)
    return ScheduleRolloutOut(at=account.exe_rollout_at)


@router.get("/notice", response_model=NoticeOut)
async def get_dashboard_notice(
    db: AsyncSession = Depends(get_db), _user=Depends(current_user),
) -> NoticeOut:
    """A dashboard banner (e.g. the scheduled rollout firing). Shown until dismissed."""
    account = await get_or_create_account(db)
    return NoticeOut(notice=account.rollout_notice, at=account.rollout_notice_at)


@router.post("/notice/dismiss", status_code=204)
async def dismiss_dashboard_notice(
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> None:
    account = await get_or_create_account(db)
    account.rollout_notice = None
    account.rollout_notice_at = None
    await db.commit()


# ── Teardown mode (pause all fault alerts while packing up a venue) ──────────
async def _teardown_status(db: AsyncSession, account) -> TeardownStatusOut:
    agents = (await db.execute(
        select(Agent).where(Agent.machine_id.is_not(None))
    )).scalars().all()
    seen = [a for a in agents if a.last_seen_at]
    online = sum(1 for a in seen if _is_online(a.last_seen_at))
    return TeardownStatusOut(
        active=bool(account.teardown_mode),
        since=account.teardown_since,
        auto_off_at=account.teardown_auto_off_at,
        online=online,
        offline=len(seen) - online,
        total=len(agents),
    )


@router.get("/teardown", response_model=TeardownStatusOut)
async def teardown_status(
    db: AsyncSession = Depends(get_db), _user=Depends(current_user),
) -> TeardownStatusOut:
    account = await get_or_create_account(db)
    return await _teardown_status(db, account)


@router.post("/teardown", response_model=TeardownStatusOut)
async def set_teardown(
    body: TeardownIn, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> TeardownStatusOut:
    """Turn teardown mode on/off. While on, the alert sweep pauses every fault
    push so packing up a venue doesn't storm the operator. `hours` sets a safety
    auto-off (default 18h; null = no auto-off)."""
    account = await get_or_create_account(db)
    now = datetime.now(tz=timezone.utc)
    if body.enabled:
        account.teardown_mode = True
        account.teardown_since = now
        account.teardown_auto_off_at = (
            now + timedelta(hours=body.hours) if body.hours and body.hours > 0 else None
        )
    else:
        account.teardown_mode = False
        account.teardown_since = None
        account.teardown_auto_off_at = None
    await db.commit()
    return await _teardown_status(db, account)


# ── Printer status log (per-card history + fleet report) ─────────────────────
def _printer_event_out(e: PrinterEvent, name: str | None = None) -> PrinterEventOut:
    return PrinterEventOut(id=e.id, agent_id=e.agent_id, agent_name=name, state=e.state,
                           prev_state=e.prev_state, detail=e.detail, raw=e.raw, at=e.created_at)


@router.get("/printer-log", response_model=list[PrinterEventOut])
async def fleet_printer_log(
    hours: int = Query(168, ge=1, le=24 * 90),
    limit: int = Query(2000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[PrinterEventOut]:
    """Every ticket-printer status change across the fleet in the window — the
    printer log report. Newest first, with the station name."""
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    rows = (await db.execute(
        select(PrinterEvent, Agent.name)
        .join(Agent, Agent.id == PrinterEvent.agent_id)
        .where(PrinterEvent.created_at >= since)
        .order_by(desc(PrinterEvent.created_at)).limit(limit)
    )).all()
    return [_printer_event_out(e, name) for e, name in rows]


@router.get("/outages")
async def station_outages(
    days: int = Query(7, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[dict]:
    """Station offline / back-online history (newest first) for the status views."""
    from sqlalchemy import text as _t
    rows = (await db.execute(_t(
        "SELECT o.agent_id, a.name, coalesce(a.station_group, 'kiosk') grp, o.kind, o.started_at, o.ended_at "
        "FROM agent_outages o JOIN agents a ON a.id = o.agent_id "
        "WHERE o.started_at > now() - make_interval(days => :d) OR o.ended_at IS NULL "
        "ORDER BY o.started_at DESC LIMIT 2000"), {"d": days})).all()
    out = []
    for r in rows:
        end = r.ended_at
        out.append({"agent_id": str(r.agent_id), "name": r.name, "group": r.grp, "kind": r.kind,
                    "started_at": r.started_at.isoformat(), "ended_at": end.isoformat() if end else None,
                    "seconds": int(((end or datetime.now(timezone.utc)) - r.started_at).total_seconds())})
    return out


@router.get("/paper")
async def paper_usage(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> dict:
    """Tickets printed (per day + per kiosk), paper used, and every real roll
    change in the window — the Paper usage report."""
    from app.models import PrinterDaily, PrinterRoll
    from app.services import paper as _paper
    today = datetime.now(_paper.SHOW_TZ).date()
    first_day = today - timedelta(days=days - 1)
    agents = {a.id: a for a in (await db.execute(select(Agent).where(Agent.claimed_at.is_not(None)))).scalars()}
    rows = (await db.execute(select(PrinterDaily).where(PrinterDaily.day >= first_day))).scalars().all()

    def _delta(last, prev, first):
        if last is None:
            return 0
        base = prev if prev is not None and prev <= last else first
        return max(0, last - (base if base is not None else last))

    per_day: dict = {}
    per_agent: dict = {}
    for r in rows:
        t = _delta(r.last_cuts, r.prev_last_cuts, r.first_cuts)
        cm = _delta(r.last_cm, r.prev_last_cm, r.first_cm)
        d = per_day.setdefault(r.day.isoformat(), {"day": r.day.isoformat(), "tickets": 0, "cm": 0})
        d["tickets"] += t
        d["cm"] += cm
        pa = per_agent.setdefault(r.agent_id, {"tickets": 0, "cm": 0, "today_tickets": 0})
        pa["tickets"] += t
        pa["cm"] += cm
        if r.day == today:
            pa["today_tickets"] = t
    since = datetime.combine(first_day, datetime.min.time(), tzinfo=_paper.SHOW_TZ)
    rolls = (await db.execute(
        select(PrinterRoll).where(PrinterRoll.created_at >= since).order_by(desc(PrinterRoll.created_at))
    )).scalars().all()
    roll_count: dict = {}
    for r in rolls:
        roll_count[r.agent_id] = roll_count.get(r.agent_id, 0) + 1
    stations = []
    for aid, a in agents.items():
        u = _paper.roll_usage(a)
        pa = per_agent.get(aid, {})
        stations.append({
            "agent_id": str(aid), "name": a.name,
            "tickets": pa.get("tickets", 0), "cm": pa.get("cm", 0),
            "today_tickets": pa.get("today_tickets", 0),
            "roll_changes": roll_count.get(aid, 0),
            "counting": bool(a.printer_cut_count),
            "roll_percent": round(100 * u["frac"], 1) if u else None,
            "tickets_left": u["tickets_left"] if u else None,
            "tickets_this_roll": u["tickets_this_roll"] if u else None,
            "roll_estimate": bool(u["partial"]) if u else None,
            "near_end": a.printer_near_end,
            "lifetime_tickets": a.printer_cut_count,
        })
    stations.sort(key=lambda x: x["name"])
    return {
        "days": sorted(per_day.values(), key=lambda d: d["day"]),
        "stations": stations,
        "rolls": [{
            "at": r.created_at.isoformat(), "agent_id": str(r.agent_id),
            "name": agents[r.agent_id].name if r.agent_id in agents else None,
            "how": r.how, "out_seconds": r.out_seconds,
            "tickets": r.prev_roll_tickets, "cm": r.prev_roll_cm, "partial": r.prev_roll_partial,
        } for r in rolls],
        "roll_cm_default": round(_paper.seed_roll_cm()),
    }


@router.get("/{agent_id}/printer-log", response_model=list[PrinterEventOut])
async def agent_printer_log(
    agent_id: uuid.UUID,
    limit: int = Query(40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[PrinterEventOut]:
    """This station's ticket-printer status changes, newest first (card history)."""
    rows = (await db.execute(
        select(PrinterEvent).where(PrinterEvent.agent_id == agent_id)
        .order_by(desc(PrinterEvent.created_at)).limit(limit)
    )).scalars().all()
    return [_printer_event_out(e) for e in rows]


@router.post("/{agent_id}/printer/new-roll", response_model=AgentOut)
async def mark_new_roll(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
) -> AgentOut:
    """Operator loaded a fresh roll: anchor the paper gauge at the current cut
    count so 'used' counts from zero again. This is the reliable reload signal
    when a roll is swapped before it runs fully empty."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Station not found")
    if agent.printer_cut_count or agent.printer_paper_cm:
        from app.services import paper as _paper
        await _paper.log_change(db, agent, "manual", datetime.now(tz=timezone.utc))
        agent.printer_roll_partial = False
        await db.commit()
        await db.refresh(agent)
    return _agent_out(agent, None, None)


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
# printer-status = safe (WMI, no ctypes). printer-probe/printer-raw/printer-test do
# native device I/O — allowed to be queued, but delivered ONLY to agent exes that
# run them crash-isolated (bootstrap ≥ 2.5); older exes get them cancelled.
# printer-test sends a small ESC/POS test ticket to the KPM180H and reports whether
# the printer accepted it and is healthy afterwards.
ALLOWED_COMMAND_KINDS = {"cam-diag", "inventory", "printer-status", "printer-probe", "printer-raw", "printer-test",
                         "power-off", "power-cancel"}
_CRASH_ISOLATED_KINDS = {"printer-probe", "printer-raw", "printer-test"}


def _supports_crash_isolation(bootstrap_version: str | None) -> bool:
    """True if the agent .exe can run device I/O in a worker subprocess."""
    if not bootstrap_version:
        return False
    try:
        parts = [int(x) for x in str(bootstrap_version).split(".")[:2]]
    except ValueError:
        return False
    return parts >= [2, 5]


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


def _strip_nul(v):
    if isinstance(v, str):
        return v.replace("\x00", "")
    if isinstance(v, dict):
        return {_strip_nul(k): _strip_nul(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_nul(x) for x in v]
    return v


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
    result = _strip_nul(body.get("result"))  # Postgres JSONB rejects \u0000
    cmd.result = result if isinstance(result, dict) else {"raw": result}
    cmd.status = "done" if body.get("ok") else "error"
    cmd.completed_at = datetime.now(tz=timezone.utc)
    if cmd.kind == "power-off" and body.get("ok"):
        delay = int((cmd.args or {}).get("delay", 60) or 0)
        agent.powered_off_at = cmd.completed_at + timedelta(seconds=delay)
    elif cmd.kind == "power-cancel" and body.get("ok"):
        agent.powered_off_at = None
    await db.commit()
    return {"ok": True}


# ── Fleet actions: test-print all, power off all, weekly shutdown ────────────
FLEET_KINDS = {"test-print": "printer-test", "power-off": "power-off", "power-cancel": "power-cancel"}
FLEET_TIMEOUT_S = 240   # a kiosk that hasn't answered by now counts as failed


class FleetIn(BaseModel):
    action: str                       # test-print | power-off | power-cancel
    agent_ids: list[uuid.UUID] | None = None   # default: every claimed kiosk
    delay: int = 60                   # power-off grace (s)
    message: str | None = None


async def queue_fleet(db: AsyncSession, action: str, requested_by: str, agent_ids=None,
                      delay: int = 60, message: str | None = None) -> FleetBatch:
    kind = FLEET_KINDS[action]
    q = select(Agent).where(Agent.machine_id.is_not(None))
    if agent_ids:
        q = q.where(Agent.id.in_(agent_ids))
    agents = list((await db.execute(q.order_by(Agent.name))).scalars())
    batch = FleetBatch(kind=action, requested_by=requested_by, total=0, skipped=[])
    db.add(batch)
    await db.flush()
    skipped = []
    for a in agents:
        if not _is_online(a.last_seen_at):
            skipped.append({"id": str(a.id), "name": a.name,
                            "why": "shut down" if a.powered_off_at else "offline"})
            continue
        if kind in _CRASH_ISOLATED_KINDS and not _supports_crash_isolation(a.bootstrap_version):
            skipped.append({"id": str(a.id), "name": a.name, "why": "agent too old"})
            continue
        args = {"batch": str(batch.id)}
        if kind == "printer-test":
            args.update({"label": a.name, "cut": "full"})
        elif kind == "power-off":
            args.update({"delay": max(0, min(3600, delay)),
                         "message": message or "NetMonitor: shutting down for the night"})
        db.add(AgentCommand(agent_id=a.id, kind=kind, args=args, status="queued",
                            requested_by=requested_by))
        batch.total += 1
    batch.skipped = skipped
    await db.commit()
    await db.refresh(batch)
    return batch


async def fleet_status(db: AsyncSession, batch: FleetBatch) -> dict:
    now = datetime.now(tz=timezone.utc)
    rows = (await db.execute(
        select(AgentCommand, Agent.name)
        .join(Agent, Agent.id == AgentCommand.agent_id)
        .where(AgentCommand.args["batch"].astext == str(batch.id))
        .order_by(Agent.name)
    )).all()
    items, ok_n, fail_n, wait_n = [], 0, 0, 0
    age = (now - batch.created_at).total_seconds() if batch.created_at else 0
    for c, name in rows:
        r = c.result or {}
        if c.status == "done" and (r.get("ok", True) is not False):
            state = "ok"
            ok_n += 1
        elif c.status in ("done", "error"):
            state = "failed"
            fail_n += 1
        elif age > FLEET_TIMEOUT_S:
            state = "no answer"
            fail_n += 1
        else:
            state = "waiting"
            wait_n += 1
        items.append({"agent_id": str(c.agent_id), "name": name, "state": state,
                      "detail": r.get("detail") or r.get("error") or r.get("note") or "",
                      "printer_state": r.get("state")})
    for s_ in batch.skipped or []:
        items.append({"agent_id": s_["id"], "name": s_["name"], "state": "skipped",
                      "detail": s_.get("why", ""), "printer_state": None})
    return {"id": str(batch.id), "action": batch.kind, "requested_by": batch.requested_by,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "total": batch.total, "ok": ok_n, "failed": fail_n, "waiting": wait_n,
            "skipped": len(batch.skipped or []), "done": wait_n == 0, "items": items}


@router.post("/fleet")
async def fleet_action(body: FleetIn, db: AsyncSession = Depends(get_db), admin=Depends(require_admin)) -> dict:
    if body.action not in FLEET_KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown action '{body.action}'")
    batch = await queue_fleet(db, body.action, getattr(admin, "email", ""), body.agent_ids,
                              body.delay, body.message)
    return await fleet_status(db, batch)


@router.get("/fleet/latest")
async def fleet_latest(action: str = Query("test-print"), db: AsyncSession = Depends(get_db),
                       _u=Depends(current_user)) -> dict | None:
    b = (await db.execute(select(FleetBatch).where(FleetBatch.kind == action)
                          .order_by(desc(FleetBatch.created_at)).limit(1))).scalars().first()
    return await fleet_status(db, b) if b else None


@router.get("/fleet/{batch_id}")
async def fleet_get(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db), _u=Depends(current_user)) -> dict:
    b = await db.get(FleetBatch, batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Not found")
    return await fleet_status(db, b)


class ShutdownScheduleIn(BaseModel):
    enabled: bool
    weekday: int = 6          # 0 = Monday … 6 = Sunday
    time: str = "22:30"       # local HH:MM
    tz: str = "America/Phoenix"
    delay: int = 120


@router.get("/fleet-schedule")
async def get_schedule(db: AsyncSession = Depends(get_db), _u=Depends(current_user)) -> dict:
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    return {"schedule": (acc.kiosk_shutdown if acc else None), "last": acc.kiosk_shutdown_last if acc else None}


@router.put("/fleet-schedule")
async def put_schedule(body: ShutdownScheduleIn, db: AsyncSession = Depends(get_db),
                       _a=Depends(require_admin)) -> dict:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(body.tz)
        hh, mm = (int(x) for x in body.time.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60 and 0 <= body.weekday <= 6
    except Exception:
        raise HTTPException(status_code=422, detail="Bad time / weekday / timezone")
    acc = await get_or_create_account(db)
    acc.kiosk_shutdown = {"enabled": body.enabled, "weekday": body.weekday, "time": f"{hh:02d}:{mm:02d}",
                          "tz": body.tz, "delay": max(30, min(1800, body.delay))}
    await db.commit()
    return {"schedule": acc.kiosk_shutdown, "last": acc.kiosk_shutdown_last}
