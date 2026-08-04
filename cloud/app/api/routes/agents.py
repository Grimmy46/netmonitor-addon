"""Site agents: registration (token issuance), the push-ingest endpoint the
agents report to, the self-update payload endpoints, and read views."""
import re
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
from app.models import Agent, PingSample, Site
from app.schemas import (
    AgentCreate,
    AgentCreated,
    AgentOut,
    AgentReport,
    AgentReportResult,
    PingPoint,
)
from app.services.sync import get_or_create_account

router = APIRouter(prefix="/agents", tags=["agents"])

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


# ── registration / management (dashboard side) ───────────────────────────────
@router.post("", response_model=AgentCreated)
async def create_agent(payload: AgentCreate, db: AsyncSession = Depends(get_db)) -> AgentCreated:
    account = await get_or_create_account(db)
    if payload.site_id is not None and await db.get(Site, payload.site_id) is None:
        raise HTTPException(status_code=400, detail="Unknown site_id")
    token = make_agent_token()
    agent = Agent(
        account_id=account.id,
        site_id=payload.site_id,
        name=payload.name,
        token_hash=hash_token(token),
        status="pending",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentCreated(id=agent.id, name=agent.name, token=token)


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
        online = _is_online(a.last_seen_at)
        out.append(
            AgentOut(
                id=a.id,
                name=a.name,
                site_id=a.site_id,
                site_name=sites.get(a.site_id) if a.site_id else None,
                status=("online" if online else ("offline" if a.last_seen_at else "pending")),
                online=online,
                version=a.version,
                hostname=a.hostname,
                os=a.os,
                last_ip=a.last_ip,
                last_target=a.last_target,
                last_seen_at=a.last_seen_at,
                latest_rtt_ms=latest,
            )
        )
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
