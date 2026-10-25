"""Link health: "Degraded" = the device is UP, but the cable/link feeding it is bad.

A switch can look fine everywhere (UniFi connected, powered, cameras behind it
online) while the link it hangs off is broken enough to drop traffic — e.g.
Ticket Box 1 on ERC port 24 negotiated at 10 Mbps with thousands of errors and
kept missing kiosk pings. This module spots that and explains it in plain words.

A wired switch / AP is DEGRADED when any of these holds:
  * slow link   — the uplink negotiated below 1 Gbps (10 / 100 Mbps)
  * errors      — the port errors on that cable (both ends) grew by
                  ERR_PER_HOUR or more in the last hour
  * ping drops  — kiosks lost it PING_DROPS+ times in the last hour while
                  UniFi still heard from it

It goes back to OK after CLEAR_AFTER with none of those.
Evaluated on every topology sync (services/topology.py); ping drops are fed
from the kiosk ping verdicts (api/routes/agents.py).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta

logger = logging.getLogger("netmonitor.linkhealth")

ERR_PER_HOUR = 100
PING_DROPS = 3
WINDOW = timedelta(hours=1)
CLEAR_AFTER = timedelta(minutes=15)
NOTIFY_AFTER = timedelta(minutes=5)       # stay degraded this long before telling anyone
RENOTIFY = timedelta(hours=6)
UNIFI_FRESH = timedelta(seconds=90)

_err: dict[str, deque] = {}       # device id -> [(t, total errors)]
_drops: dict[str, deque] = {}     # device id -> [t of ping-down verdicts]
_ok_since: dict[str, datetime] = {}


def unifi_alive(dev, now: datetime) -> bool:
    """UniFi says connected AND heard from it within the last ~90 s."""
    return (getattr(dev, "unifi_state", None) == 1 and dev.unifi_last_seen is not None
            and now - dev.unifi_last_seen <= UNIFI_FRESH)


def note_ping_down(dev_id, now: datetime) -> None:
    q = _drops.setdefault(str(dev_id), deque(maxlen=50))
    q.append(now)


def _drops_in_window(k: str, now: datetime) -> int:
    q = _drops.get(k)
    if not q:
        return 0
    while q and now - q[0] > WINDOW:
        q.popleft()
    return len(q)


def _port(rd: dict | None, idx) -> dict | None:
    if not rd or idx is None:
        return None
    for p in rd.get("port_table") or []:
        if p.get("port_idx") == idx:
            return p
    return None


def _errs(p: dict | None) -> int:
    if not p:
        return 0
    return int(p.get("rx_errors") or 0) + int(p.get("tx_errors") or 0)


def _speed_txt(mbps: int) -> str:
    return f"{mbps // 1000} Gbps" if mbps >= 1000 else f"{mbps} Mbps"


def evaluate(dev, rd: dict, parent_rd: dict | None, now: datetime) -> None:
    """Update dev.link_status / link_reason / link_since / link_errors in place."""
    if dev.device_type not in ("switch", "ap") or dev.uplink_type == "wireless":
        dev.link_status, dev.link_reason = None, None
        return
    if rd.get("state") != 1:
        return  # offline / provisioning: the down/up logic owns it
    k = str(dev.id)
    reasons: list[str] = []

    # 1) negotiated speed (both ends agree; prefer the parent's port record)
    pp = _port(parent_rd, dev.uplink_port)
    own = _port(rd, dev.local_port)
    spd = int((pp or {}).get("speed") or (own or {}).get("speed") or dev.uplink_speed_mbps or 0)
    if 0 < spd < 1000:
        reasons.append(f"link runs at {_speed_txt(spd)} (should be 1 Gbps or more)")

    # 2) errors on that cable, both ends
    total = _errs(pp) + _errs(own)
    dev.link_errors = total if (pp or own) else dev.link_errors
    q = _err.setdefault(k, deque(maxlen=80))
    if q and total < q[-1][1]:
        q.clear()          # counters reset (reboot / re-plug)
    q.append((now, total))
    while len(q) > 1 and now - q[0][0] > WINDOW:
        q.popleft()
    grew = total - q[0][1] if q else 0
    if grew >= ERR_PER_HOUR:
        reasons.append(f"{grew:,} new errors on that cable in the last hour ({total:,} total)")
    elif reasons and total >= ERR_PER_HOUR:
        reasons.append(f"{total:,} errors on the port")

    # 3) repeated ping drops while UniFi still hears it
    n = _drops_in_window(k, now)
    if n >= PING_DROPS:
        reasons.append(f"dropped kiosk pings {n} times in the last hour while UniFi still saw it")

    if reasons:
        _ok_since.pop(k, None)
        if dev.link_status != "degraded":
            dev.link_since = now
            logger.info("Link degraded: %s — %s", dev.name, "; ".join(reasons))
        dev.link_status = "degraded"
        dev.link_reason = "; ".join(reasons)
    elif dev.link_status == "degraded":
        first_ok = _ok_since.setdefault(k, now)
        if now - first_ok >= CLEAR_AFTER:
            logger.info("Link healthy again: %s", dev.name)
            dev.link_status, dev.link_reason, dev.link_since = "ok", None, None
            _ok_since.pop(k, None)
    else:
        dev.link_status = "ok"


def feed_text(dev, parent) -> str:
    """'fed from ERC #0917, port 24'"""
    from app.services.names import split_name
    if parent is None:
        return ""
    label, tag = split_name(parent.name or parent.model)
    pl = f"{label} #{tag}" if tag else label
    port = f", port {dev.uplink_port}" if dev.uplink_port else ""
    return f"fed from {pl}{port}"


def trouble_text(dev, parent) -> str:
    """Plain-words explanation for an alert about a device that's up but troubled."""
    feed = feed_text(dev, parent)
    why = dev.link_reason or "not answering kiosk pings"
    where = f" Its cable ({feed})" if feed else " Its uplink cable"
    return (f"It's powered and UniFi still sees it — this is a LINK problem, not power.{where}: {why}. "
            "Check or replace that cable / both connectors.")


async def notify(db, site, devices: list, now: datetime) -> None:
    """Tell RCS-IT (network notice) once a link has been degraded for a few minutes."""
    from app.services import closure
    if closure.phase(now) == "closed":
        return
    by_mac = {d.mac.lower(): d for d in devices if d.mac}
    todo = [d for d in devices if d.link_status == "degraded" and d.link_since
            and now - d.link_since >= NOTIFY_AFTER
            and (d.link_notified_at is None or now - d.link_notified_at >= RENOTIFY
                 or d.link_notified_at < d.link_since)
            and not d.manual_dormant and not (d.muted_until and d.muted_until > now)]
    if not todo:
        return
    from app.services.names import split_name
    from app.services.notify import post_group, send_push
    lines = []
    for d in todo[:6]:
        label, tag = split_name(d.name or d.model)
        name = f"{label} #{tag}" if tag else label
        parent = by_mac.get((d.uplink_mac or "").lower())
        feed = feed_text(d, parent)
        lines.append(f"• {name}{f' ({feed})' if feed else ''}: {d.link_reason}.")
        d.link_notified_at = now
    more = f"\n+{len(todo) - 6} more on the map." if len(todo) > 6 else ""
    head = "🟠 DEGRADED LINK" + ("S" if len(todo) > 1 else "")
    text = (f"{head} — up and powered, but the cable is bad:\n" + "\n".join(lines) + more +
            "\nCheck or replace the cable / connectors.")
    await db.commit()
    try:
        await post_group(db, text)
        await send_push(db, {"title": head, "body": "\n".join(lines)[:300], "tag": f"link-{todo[0].id}",
                             "url": f"/#/site/{site.id}?focus={todo[0].id}", "urgency": "normal"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("degraded-link notice failed: %s", exc)
