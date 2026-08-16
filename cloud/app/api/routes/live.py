"""Live landing page API: probe targets (the old tool's TARGETS tab), the
designated probe kiosk, and the chart feed.

Vantage model: the feed prefers samples from the designated kiosk (the on-lot
truth). When the kiosk is asleep (no local sample within the freshness window)
it falls back per-target to the server's own prober so the page stays alive
overnight — clearly flagged so the UI can badge it as the cloud view.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user, require_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Agent, ProbeSample, ProbeTarget, WanIncident
from app.schemas import WanIncidentOut, WanStatusOut
from app.services.sync import get_or_create_account

router = APIRouter(prefix="/live", tags=["live"])


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


DEFAULT_TARGETS = [
    ("ping", "Site gateway", "gateway"),
    ("ping", "Cloudflare DNS", "1.1.1.1"),
    ("ping", "Google DNS", "8.8.4.4"),
    ("http", "SignaPay payments", "https://signapay.transactiongateway.com/merchants/login.php"),
    ("http", "Funcard kiosk", "https://rcs.funcardapp.com"),
]


async def ensure_default_targets(db: AsyncSession):
    """Seed the classic target set the first time anyone looks."""
    account = await get_or_create_account(db)
    existing = (
        await db.execute(select(ProbeTarget).where(ProbeTarget.account_id == account.id))
    ).scalars().first()
    if existing is None:
        for i, (kind, label, target) in enumerate(DEFAULT_TARGETS):
            db.add(ProbeTarget(account_id=account.id, kind=kind, label=label,
                               target=target, enabled=True, sort=i))
        await db.commit()
    return account


class TargetOut(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    target: str
    enabled: bool
    sort: int


class TargetIn(BaseModel):
    kind: str = "ping"  # ping | http
    label: str
    target: str
    enabled: bool = True


class TargetPatch(BaseModel):
    kind: str | None = None
    label: str | None = None
    target: str | None = None
    enabled: bool | None = None
    sort: int | None = None


class ProbeAgentIn(BaseModel):
    agent_id: uuid.UUID | None = None


def _target_out(t: ProbeTarget) -> TargetOut:
    return TargetOut(id=t.id, kind=t.kind, label=t.label, target=t.target,
                     enabled=t.enabled, sort=t.sort)


@router.get("/targets", response_model=list[TargetOut])
async def list_targets(
    db: AsyncSession = Depends(get_db), _user=Depends(current_user)
) -> list[TargetOut]:
    account = await ensure_default_targets(db)
    rows = (
        await db.execute(
            select(ProbeTarget)
            .where(ProbeTarget.account_id == account.id)
            .order_by(ProbeTarget.sort, ProbeTarget.created_at)
        )
    ).scalars()
    return [_target_out(t) for t in rows]


@router.post("/targets", response_model=TargetOut)
async def add_target(
    body: TargetIn, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)
) -> TargetOut:
    if body.kind not in ("ping", "http"):
        raise HTTPException(status_code=422, detail="kind must be ping or http")
    if not body.target.strip():
        raise HTTPException(status_code=422, detail="target is required")
    account = await ensure_default_targets(db)
    max_sort = max(
        [t.sort for t in (await db.execute(
            select(ProbeTarget).where(ProbeTarget.account_id == account.id)
        )).scalars()] or [0]
    )
    t = ProbeTarget(account_id=account.id, kind=body.kind,
                    label=body.label.strip() or body.target.strip(),
                    target=body.target.strip(), enabled=body.enabled, sort=max_sort + 1)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _target_out(t)


@router.patch("/targets/{target_id}", response_model=TargetOut)
async def update_target(
    target_id: uuid.UUID, body: TargetPatch,
    db: AsyncSession = Depends(get_db), _admin=Depends(require_admin),
) -> TargetOut:
    t = await db.get(ProbeTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Target not found")
    if body.kind is not None:
        if body.kind not in ("ping", "http"):
            raise HTTPException(status_code=422, detail="kind must be ping or http")
        t.kind = body.kind
    if body.label is not None:
        t.label = body.label.strip()
    if body.target is not None:
        if not body.target.strip():
            raise HTTPException(status_code=422, detail="target is required")
        t.target = body.target.strip()
    if body.enabled is not None:
        t.enabled = body.enabled
    if body.sort is not None:
        t.sort = body.sort
    await db.commit()
    await db.refresh(t)
    return _target_out(t)


@router.delete("/targets/{target_id}", status_code=204)
async def delete_target(
    target_id: uuid.UUID, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)
) -> None:
    t = await db.get(ProbeTarget, target_id)
    if t is not None:
        await db.delete(t)  # samples cascade
        await db.commit()


@router.post("/probe-agent")
async def set_probe_agent(
    body: ProbeAgentIn, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)
) -> dict:
    account = await get_or_create_account(db)
    if body.agent_id is not None:
        agent = await db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
    account.probe_agent_id = body.agent_id
    await db.commit()
    return {"probe_agent_id": str(body.agent_id) if body.agent_id else None}


class FeedSample(BaseModel):
    ts: float  # epoch seconds
    ms: float | None


class FeedTarget(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    target: str
    enabled: bool
    vantage: str  # local | cloud | none
    ok: bool | None  # latest sample answered? (None = no data yet)
    last_ms: float | None
    loss_pct: float
    samples: list[FeedSample]


class FeedOut(BaseModel):
    generated_at: datetime
    window_minutes: int
    probe_agent: dict | None  # {id, name, online}
    targets: list[FeedTarget]


@router.get("/feed", response_model=FeedOut)
async def feed(
    minutes: int = Query(10, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> FeedOut:
    st = get_settings()
    now = _now()
    account = await ensure_default_targets(db)
    targets = list(
        (await db.execute(
            select(ProbeTarget)
            .where(ProbeTarget.account_id == account.id)
            .order_by(ProbeTarget.sort, ProbeTarget.created_at)
        )).scalars()
    )

    probe_agent = None
    if account.probe_agent_id:
        a = await db.get(Agent, account.probe_agent_id)
        if a is not None:
            from app.api.routes.agents import _is_online  # same rule as Kiosks tab
            probe_agent = {"id": str(a.id), "name": a.name,
                          "online": _is_online(a.last_seen_at)}

    cutoff = now - timedelta(minutes=minutes)
    fresh_cutoff = now - timedelta(seconds=st.live_local_fresh_seconds)
    rows = (
        await db.execute(
            select(ProbeSample)
            .where(ProbeSample.ts >= cutoff,
                   ProbeSample.target_id.in_([t.id for t in targets] or [uuid.uuid4()]))
            .order_by(ProbeSample.ts)
        )
    ).scalars()

    local: dict[uuid.UUID, list[ProbeSample]] = {}
    cloud: dict[uuid.UUID, list[ProbeSample]] = {}
    for s in rows:
        (local if s.agent_id is not None else cloud).setdefault(s.target_id, []).append(s)

    out: list[FeedTarget] = []
    for t in targets:
        loc, cld = local.get(t.id, []), cloud.get(t.id, [])
        # Prefer the on-lot vantage while it's actually flowing.
        if loc and loc[-1].ts >= fresh_cutoff:
            picked, vantage = loc, "local"
        elif cld:
            picked, vantage = cld, "cloud"
        elif loc:
            picked, vantage = loc, "local"  # stale but better than nothing
        else:
            picked, vantage = [], "none"
        n = len(picked)
        lost = sum(1 for s in picked if s.ms is None)
        out.append(FeedTarget(
            id=t.id, kind=t.kind, label=t.label, target=t.target, enabled=t.enabled,
            vantage=vantage,
            ok=(picked[-1].ms is not None) if picked else None,
            last_ms=next((s.ms for s in reversed(picked) if s.ms is not None), None),
            loss_pct=round(100.0 * lost / n, 2) if n else 0.0,
            samples=[FeedSample(ts=s.ts.timestamp(), ms=s.ms) for s in picked],
        ))
    return FeedOut(generated_at=now, window_minutes=minutes,
                   probe_agent=probe_agent, targets=out)


def _incident_out(inc: WanIncident, now: datetime) -> WanIncidentOut:
    ongoing = inc.ended_at is None
    end = inc.ended_at or now
    return WanIncidentOut(
        id=inc.id, kind=inc.kind, started_at=inc.started_at, ended_at=inc.ended_at,
        ongoing=ongoing,
        duration_seconds=int((end - inc.started_at).total_seconds()),
        peak_loss_pct=inc.peak_loss_pct, peak_latency_ms=inc.peak_latency_ms,
        worst_target=inc.worst_target, detail=inc.detail,
    )


@router.get("/wan-incidents", response_model=list[WanIncidentOut])
async def wan_incidents(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
) -> list[WanIncidentOut]:
    """Logged WAN/internet brownouts (external targets degraded while the LAN was
    fine) — the evidence trail for the ISP. Newest first; ongoing ones included."""
    now = _now()
    account = await get_or_create_account(db)
    since = now - timedelta(days=days)
    rows = (await db.execute(
        select(WanIncident)
        .where(WanIncident.account_id == account.id, WanIncident.started_at >= since)
        .order_by(WanIncident.started_at.desc())
        .limit(limit)
    )).scalars()
    return [_incident_out(i, now) for i in rows]


@router.get("/wan-status", response_model=WanStatusOut)
async def wan_status(
    db: AsyncSession = Depends(get_db), _user=Depends(current_user)
) -> WanStatusOut:
    """Current WAN health: a live brownout (open incident) or clear."""
    now = _now()
    account = await get_or_create_account(db)
    open_inc = (await db.execute(
        select(WanIncident)
        .where(WanIncident.account_id == account.id, WanIncident.ended_at.is_(None))
        .order_by(WanIncident.started_at.desc())
        .limit(1)
    )).scalars().first()
    if open_inc is not None:
        return WanStatusOut(
            state="brownout", since=open_inc.started_at,
            detail=open_inc.detail, incident=_incident_out(open_inc, now),
        )
    return WanStatusOut(state="clear", since=None, detail=None, incident=None)
