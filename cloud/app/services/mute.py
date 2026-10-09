"""Per-device notification mute ("being worked on"). A muted device and
everything behind it (downstream gear + kiosks plugged into those switches)
sends no pushes / Signal / fast alerts. Status, maps and counts are untouched."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Device


def _norm(mac: str | None) -> str:
    return (mac or "").lower().replace("-", ":")


async def muted_sets(db: AsyncSession, now: datetime) -> tuple[set, set]:
    """(muted device ids, muted agent ids)."""
    devs = (await db.execute(select(Device))).scalars().all()
    roots = {d.id for d in devs if d.muted_until and d.muted_until > now}
    if not roots:
        return set(), set()
    by_mac = {_norm(d.mac): d for d in devs if d.mac}
    parent = {d.id: by_mac.get(_norm(d.uplink_mac)) for d in devs}
    muted = set()
    for d in devs:
        cur, hops = d, 0
        while cur is not None and hops < 12:
            if cur.id in roots:
                muted.add(d.id)
                break
            p = parent.get(cur.id)
            cur = p if p is not None and p.id != cur.id else None
            hops += 1
    muted_macs = {_norm(d.mac) for d in devs if d.id in muted and d.mac}
    agents = (await db.execute(select(Agent.id, Agent.switch_mac))).all()
    return muted, {a for a, m in agents if m and _norm(m) in muted_macs}
