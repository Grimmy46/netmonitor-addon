"""Kiosk pings decide up/down for UniFi gear (switches, APs, gateways).

Every kiosk agent sweeps its assigned devices every 30 s (/agents/targets
hands each device to K online kiosks, so each device gets a ping every few
seconds from different vantage points). UniFi's own view lags by minutes;
these pings don't.

Rules (per device):
- UP   as soon as ANY kiosk gets a reply.
- DOWN when nobody got a reply for OK_WINDOW seconds AND at least 2 different
  healthy kiosks (ones that reached something else in the same sweep) failed.
- No fresh ping info (no IP / kiosks asleep / never answered) → None → UniFi decides.
Single-process app, so this state lives in memory; after a restart the next
few sweeps rebuild it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

K_VANTAGE = 6          # kiosks assigned to ping each device
OK_WINDOW = 40         # s without any reply before a device can be called down
VOTES = 2              # distinct kiosks that must fail
STALE = 120            # s — older ping info is ignored (UniFi decides)

_ok: dict[str, datetime] = {}                    # dev id → last reply
_fail: dict[str, dict[str, datetime]] = {}       # dev id → {agent id: last fail}
_first_fail: dict[str, datetime] = {}            # dev id → first fail since last reply
_down: set[str] = set()


def record(dev_id, agent_id, reachable: bool, agent_ok: bool, now: datetime) -> str | None:
    """Feed one ping result. Returns "up" / "down" on a verdict change."""
    k = str(dev_id)
    if reachable:
        _ok[k] = now
        _fail.pop(k, None)
        _first_fail.pop(k, None)
        if k in _down:
            _down.discard(k)
            return "up"
        return None
    if not agent_ok:
        return None  # this kiosk reached nothing at all — its own network is the problem
    votes = _fail.setdefault(k, {})
    votes[str(agent_id)] = now
    _first_fail.setdefault(k, now)
    last_ok = _ok.get(k)
    if k in _down:
        return None
    recent = [t for t in votes.values() if (now - t).total_seconds() <= 90]
    # Only gear the kiosks HAVE reached before (since restart) can be called
    # down by ping — devices on subnets the kiosks can't route to (never
    # answer) stay on UniFi's word.
    if last_ok is None:
        return None
    if len(recent) >= VOTES and (now - last_ok).total_seconds() >= OK_WINDOW:
        _down.add(k)
        return "down"
    return None


def verdict(dev_id, now: datetime) -> bool | None:
    """True = kiosks reach it, False = kiosks say it's down, None = no fresh info."""
    k = str(dev_id)
    ok = _ok.get(k)
    if k in _down:
        last_fail = max((_fail.get(k) or {}).values(), default=None)
        if last_fail is not None and (now - last_fail).total_seconds() <= STALE:
            return False
        return None
    if ok is not None and (now - ok).total_seconds() <= STALE:
        return True
    return None


def first_fail(dev_id) -> datetime | None:
    return _first_fail.get(str(dev_id))


def adjust(dev_id, unifi_online: bool | None, now: datetime) -> bool | None:
    """What a UniFi refresh should apply: fresh ping info wins over UniFi."""
    v = verdict(dev_id, now)
    return unifi_online if v is None else v


def assign(device_ids: list, agent_ids: list, me) -> set[str]:
    """Rendezvous hashing: the K kiosks that ping each device (stable as
    kiosks come and go). Returns the device ids assigned to `me`."""
    import hashlib
    me = str(me)
    agents = sorted({str(a) for a in agent_ids} | {me})
    out: set[str] = set()
    for d in device_ids:
        ds = str(d)
        ranked = sorted(agents, key=lambda a: hashlib.md5(f"{a}|{ds}".encode()).digest())
        if me in ranked[:K_VANTAGE]:
            out.add(ds)
    return out


def snapshot(now: datetime) -> dict:
    return {"down": sorted(_down), "tracked": len(_ok | {k: now for k in _fail}),
            "fresh_ok": sum(1 for t in _ok.values() if now - t <= timedelta(seconds=STALE))}
