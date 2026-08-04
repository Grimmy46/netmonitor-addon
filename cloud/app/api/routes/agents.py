"""Site agents: registration (token issuance), the push-ingest endpoint the
agents report to, the self-update payload endpoints, and read views."""
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import hash_token, make_agent_token
from app.models import Account, Agent, PingSample, Site
from app.schemas import (
    AgentCreate,
    AgentOut,
    AgentReport,
    AgentReportResult,
    BulkResult,
    BulkStationsIn,
    EnrollAddIn,
    EnrollClaimIn,
    EnrollmentPinOut,
    EnrollResult,
    EnrollStationOut,
    EnrollStationsIn,
    PingPoint,
)
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
async def create_agent(payload: AgentCreate, db: AsyncSession = Depends(get_db)) -> AgentOut:
    """Create a *station* — a named slot a kiosk claims on first run. No token is
    issued here; claiming (via the enrollment PIN) mints the token on the kiosk."""
    account = await get_or_create_account(db)
    if payload.site_id is not None and await db.get(Site, payload.site_id) is None:
        raise HTTPException(status_code=400, detail="Unknown site_id")
    agent = Agent(
        account_id=account.id,
        site_id=payload.site_id,
        name=payload.name,
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
async def bulk_create_stations(body: BulkStationsIn, db: AsyncSession = Depends(get_db)) -> BulkResult:
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
        db.add(Agent(account_id=account.id, name=name, token_hash="", status="pending"))
        existing.add(name)
        created += 1
    await db.commit()
    return BulkResult(created=created, skipped=skipped)


@router.post("/{agent_id}/release", response_model=AgentOut)
async def release_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> AgentOut:
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


@router.get("", response_model=list[AgentOut])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[AgentOut]:
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
async def delete_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
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
    if request.client:
        agent.last_ip = request.client.host

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
    await db.commit()
    # Phase 3 will return pending commands here for the agent to execute.
    return AgentReportResult(ok=True, stored=stored, commands=[])


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
async def get_enrollment_pin(db: AsyncSession = Depends(get_db)) -> EnrollmentPinOut:
    _, pin = await _get_pin(db)
    return EnrollmentPinOut(pin=pin)


@router.post("/enrollment/regenerate", response_model=EnrollmentPinOut)
async def regenerate_enrollment_pin(db: AsyncSession = Depends(get_db)) -> EnrollmentPinOut:
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
    await db.commit()
    return EnrollResult(token=token, name=agent.name)


@router.post("/enroll/add", response_model=EnrollResult)
async def enroll_add(body: EnrollAddIn, db: AsyncSession = Depends(get_db)) -> EnrollResult:
    account = await _require_pin(db, body.pin)
    agent = Agent(account_id=account.id, name=body.name, token_hash="", status="pending")
    token = await _claim(agent, body.hostname, body.machine_id)
    db.add(agent)
    await db.commit()
    return EnrollResult(token=token, name=agent.name)
