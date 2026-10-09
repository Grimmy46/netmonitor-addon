"""Self-building network map: who plugs into whom, WAN link health, root cause.

The Network Integration API (used by sync.py) gives device up/down but no
uplinks. The SAME console API key also works on the controller's classic
endpoint `GET /proxy/network/api/s/<site>/stat/device`, which returns, per
device, its uplink (parent MAC + the parent's port number + our own port), and
for the gateway the live state of WAN1/WAN2. This module:

* `refresh_site_topology` — pulls that once per site and stores the uplink of
  every device (the LAST KNOWN uplink is kept while a device is offline, which
  is exactly what's needed to say where an outage starts) and the site's WAN
  link status.
* `analyze` — pure function: turns a site's devices into a tree (nodes + edges)
  and groups every offline device under the highest offline device above it,
  so "12 things are down" becomes "the Fender switch is down (12 behind it),
  plugged into Kiosk 8 port 24".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt
from app.models import Device, Site, StatusEvent, UnifiConsole
from app.services.sync import _apply_online_state

logger = logging.getLogger("netmonitor.topology")

GATEWAY_TYPES = {"uxg", "ugw", "udm", "usg"}
TYPE_MAP = {"usw": "switch", "uap": "ap", "uxg": "gateway", "ugw": "gateway",
            "udm": "gateway", "usg": "gateway"}


def _norm_mac(mac: Any) -> str | None:
    return str(mac).strip().lower() if mac else None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Fetch ──────────────────────────────────────────────────────────────────
async def fetch_site_devices(console: UnifiConsole, site_ref: str) -> list[dict]:
    """Classic controller device list for one site (uplinks, ports, WAN)."""
    p = urlparse(console.base_url)
    url = f"{p.scheme}://{p.netloc}/proxy/network/api/s/{site_ref}/stat/device"
    headers = {"X-API-KEY": decrypt(console.encrypted_api_key), "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30.0, verify=console.verify_tls) as client:
        resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if isinstance(data, dict) else []


async def fetch_site_clients(console: UnifiConsole, site_ref: str) -> list[dict]:
    """Classic controller client list (sw_mac / sw_port per wired client)."""
    p = urlparse(console.base_url)
    url = f"{p.scheme}://{p.netloc}/proxy/network/api/s/{site_ref}/stat/sta"
    headers = {"X-API-KEY": decrypt(console.encrypted_api_key), "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=45.0, verify=console.verify_tls) as client:
        resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if isinstance(data, dict) else []


_KIOSK_LOC_EVERY_S = 300
_kiosk_loc_at: dict = {}


_CAM_HOST = re.compile(r"^C\d{3}[A-Z]?$", re.I)   # Tapo C110 / C200 / C310 … hostnames


async def _upsert_cameras(db: AsyncSession, site: Site, clients: list[dict], now: datetime) -> None:
    """Tapo cameras announce themselves as e.g. 'C110' — record each by MAC
    with its current IP and nearest AP, giving new ones the next relay slot."""
    from app.models import Camera
    cams = [c for c in clients if _CAM_HOST.match(str(c.get("hostname") or ""))]
    if not cams:
        return
    have = {c.mac: c for c in (await db.execute(select(Camera))).scalars()}
    used = {c.slot for c in have.values()}
    for c in cams:
        mac = _norm_mac(c.get("mac"))
        if not mac:
            continue
        cam = have.get(mac)
        if cam is None:
            slot = next(i for i in range(1, 100) if i not in used)
            used.add(slot)
            cam = Camera(mac=mac, slot=slot, site_id=site.id, model=str(c.get("hostname")),
                         name=f"Camera {slot}", enabled=True)
            db.add(cam)
            have[mac] = cam
        cam.ip = c.get("ip") or cam.ip
        cam.ap_name = c.get("last_uplink_name") or cam.ap_name
        cam.site_id = site.id
        cam.last_seen_at = now
    await db.flush()


async def refresh_kiosk_locations(db: AsyncSession, console: UnifiConsole, site: Site,
                                  now: datetime) -> int:
    """Which switch + port each kiosk is plugged into, from UniFi's client list
    matched on the kiosk's Windows hostname (or LAN IP). Every 5 min."""
    from app.models import Agent
    last = _kiosk_loc_at.get(site.id)
    if last and (now - last).total_seconds() < _KIOSK_LOC_EVERY_S:
        return 0
    _kiosk_loc_at[site.id] = now
    agents = list((await db.execute(select(Agent).where(Agent.hostname.is_not(None)))).scalars())
    clients = await fetch_site_clients(console, site.unifi_site_ref)
    try:
        async with db.begin_nested():
            await _upsert_cameras(db, site, clients, now)
    except Exception as exc:  # noqa: BLE001 — cameras must never break kiosk location
        logger.warning("camera discovery failed for %s: %s", site.name, exc)
    if not agents:
        return 0
    by_host = {}
    by_ip = {}
    for c in clients:
        if c.get("hostname"):
            by_host[str(c["hostname"]).lower()] = c
        if c.get("ip"):
            by_ip[c["ip"]] = c
    if not by_host and not by_ip:
        return 0
    names = {(_norm_mac(d.mac)): d.name for d in (await db.execute(
        select(Device).where(Device.site_id == site.id))).scalars() if d.mac}
    n = 0
    for a in agents:
        c = by_host.get((a.hostname or "").lower()) or (by_ip.get(a.lan_ip) if a.lan_ip else None)
        if c is None:
            continue
        a.lan_ip = c.get("ip") or a.lan_ip
        a.lan_mac = _norm_mac(c.get("mac")) or a.lan_mac
        sw = _norm_mac(c.get("sw_mac")) if c.get("is_wired", True) else None
        if sw:
            a.switch_mac = sw
            a.switch_port = _int(c.get("sw_port"))
            a.switch_name = names.get(sw) or c.get("last_uplink_name") or a.switch_name
        a.lan_seen_at = now
        n += 1
    return n


# ── WAN ────────────────────────────────────────────────────────────────────
def _wan_links(gw: dict) -> list[dict]:
    uptime = gw.get("uptime_stats") or {}
    last = gw.get("last_wan_status") or {}
    links = []
    for key, field_name, stats_key in (("WAN1", "wan1", "WAN"), ("WAN2", "wan2", "WAN2")):
        w = gw.get(field_name)
        if not isinstance(w, dict) or not w.get("enable", True):
            continue
        st = uptime.get(stats_key) or {}
        links.append({
            "key": key,
            "ifname": w.get("ifname") or w.get("name"),
            "up": bool(w.get("up")),
            "status": last.get(stats_key),          # "online" / "offline" per the gateway
            "active": bool(w.get("is_uplink")),     # carrying traffic right now
            "ip": w.get("ip"),
            "latency_ms": w.get("latency"),
            "availability": st.get("availability", w.get("availability")),
            "speed_mbps": w.get("speed"),
            "media": w.get("media"),
        })
    return links


def _pick_gateway(raw: list[dict]) -> dict | None:
    """With an HA pair, the active unit is the one whose WANs are up."""
    gws = [d for d in raw if d.get("type") in GATEWAY_TYPES]
    if not gws:
        return None
    def score(g):
        links = _wan_links(g)
        return (g.get("state") == 1, sum(1 for x in links if x["up"]), bool(g.get("last_wan_status")))
    return max(gws, key=score)


# ── Refresh one site ───────────────────────────────────────────────────────
async def refresh_site_topology(db: AsyncSession, console: UnifiConsole, site: Site,
                                now: datetime) -> dict:
    raw = await fetch_site_devices(console, site.unifi_site_ref)
    by_mac = {_norm_mac(d.get("mac")): d for d in raw if d.get("mac")}

    # Lock this site's device rows in primary-key order (same order the kiosk
    # device-report uses) so the two writers can never deadlock.
    devices = (
        await db.execute(
            select(Device).where(Device.site_id == site.id)
            .order_by(Device.id).with_for_update()
        )
    ).scalars().all()

    updated = 0
    went_down: list[Device] = []
    came_up: list[Device] = []
    wan_down: list[str] = []
    wan_up: list[str] = []
    for dev in devices:
        rd = by_mac.get(_norm_mac(dev.mac))
        if rd is None:
            continue
        was_online = dev.is_online
        # Faster up/down than the 5-minute inventory sync: 1 = connected,
        # 0 = disconnected; transitional states (upgrading, provisioning…) are
        # left alone.
        state = rd.get("state")
        # Fresh kiosk pings win over UniFi's (laggy) state — pingwatch.py.
        from app.services import pingwatch
        if state in (0, 1):
            _apply_online_state(dev, pingwatch.adjust(dev.id, state == 1, now), now)

        up = rd.get("uplink") if isinstance(rd.get("uplink"), dict) else {}
        if (not up.get("uplink_mac") and state != 1 and rd.get("type") not in GATEWAY_TYPES
                and isinstance(rd.get("last_uplink"), dict)):
            up = rd["last_uplink"]   # offline: remember where it WAS plugged in
        if up.get("uplink_mac"):
            dev.uplink_mac = _norm_mac(up.get("uplink_mac"))
            dev.uplink_port = _int(up.get("uplink_remote_port"))
            dev.local_port = _int(up.get("port_idx"))
            dev.uplink_type = up.get("type") or dev.uplink_type
            dev.uplink_speed_mbps = _int(up.get("speed")) or dev.uplink_speed_mbps
        elif rd.get("type") in GATEWAY_TYPES:
            dev.uplink_mac, dev.uplink_port, dev.local_port = None, None, None
        if rd.get("uplink_depth") is not None:
            dev.uplink_depth = _int(rd.get("uplink_depth"))
        ports = rd.get("port_table")
        if isinstance(ports, list) and ports:
            dev.ports_total = len(ports)
            dev.ports_up = sum(1 for p in ports if p.get("up"))
        if rd.get("type") in TYPE_MAP and not dev.device_type:
            dev.device_type = TYPE_MAP[rd["type"]]
        ls = _int(rd.get("last_seen"))
        if ls:
            dev.unifi_last_seen = datetime.fromtimestamp(ls, tz=timezone.utc)
        dev.topology_at = now
        updated += 1
        if was_online is True and dev.is_online is False:
            went_down.append(dev)
        elif was_online is False and dev.is_online is True:
            came_up.append(dev)

    # WAN links from the (active) gateway, with up/down transitions logged.
    gw = _pick_gateway(raw)
    if gw is not None:
        links = _wan_links(gw)
        prev = {x["key"]: x.get("up") for x in ((site.wan_status or {}).get("links") or [])}
        for link in links:
            was = prev.get(link["key"])
            if was is not None and was != link["up"]:
                (wan_up if link["up"] else wan_down).append(link["key"])
                db.add(StatusEvent(
                    account_id=site.account_id, site_id=site.id,
                    name=f"{site.name} {link['key']}", kind="wan",
                    event="online" if link["up"] else "offline", ts=now,
                ))
        site.wan_status = {
            "gateway": gw.get("name") or gw.get("model"),
            "gateway_model": gw.get("model"),
            "ha_units": sum(1 for d in raw if d.get("type") in GATEWAY_TYPES),
            "links": links,
        }
        site.wan_status_at = now
    await db.commit()
    # Super-fast heads-up push to admins (no confirm wait).
    try:
        from app.services import fastalert
        await fastalert.notify(db, site, went_down, came_up, wan_down, wan_up, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fast alert failed: %s", exc)
    try:
        await refresh_kiosk_locations(db, console, site, now)
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — never break the device refresh
        await db.rollback()
        logger.warning("kiosk location refresh failed for %s: %s", site.name, exc)
    return {"devices": updated, "wan": bool(gw)}


async def refresh_all(db: AsyncSession) -> dict:
    now_ = datetime.now(tz=timezone.utc)
    # Plain tuples up front: a failed site rolls the session back, which
    # expires every loaded object, so each site is re-loaded with `get`.
    todo = (
        await db.execute(select(Site.id, Site.console_id, Site.name).where(
            Site.unifi_site_ref.is_not(None), Site.console_id.is_not(None)))
    ).all()
    out = {"sites": 0, "devices": 0, "errors": 0}
    for site_id, console_id, name in todo:
        try:
            c = await db.get(UnifiConsole, console_id)
            s = await db.get(Site, site_id)
            if c is None or s is None:
                continue
            r = await refresh_site_topology(db, c, s, now_)
            out["sites"] += 1
            out["devices"] += r["devices"]
        except Exception as exc:
            await db.rollback()
            out["errors"] += 1
            logger.warning("Topology refresh failed for %s: %s", name, exc)
    return out


# ── Analysis (pure) ────────────────────────────────────────────────────────
@dataclass
class Outage:
    root: Device
    affected: list[Device] = field(default_factory=list)   # root + everything behind it
    parent: Device | None = None


def is_dormant(d: Device, now: datetime) -> bool:
    if d.manual_dormant:
        return True
    # Closure-aware: time inside a planned closure doesn't count.
    from app.services import closure
    return d.is_online is False and d.offline_since is not None and \
        closure.device_dormant(False, d.is_online, d.offline_since, now)


def analyze(devices: list[Device], now: datetime) -> dict:
    by_mac = {_norm_mac(d.mac): d for d in devices if d.mac}
    parent_of = {d.id: by_mac.get(d.uplink_mac) if d.uplink_mac else None for d in devices}

    def down(d: Device) -> bool:
        return d.is_online is False and not is_dormant(d, now)

    def root_of(d: Device) -> Device:
        root, seen, cur = d, {d.id}, parent_of.get(d.id)
        while cur is not None and cur.id not in seen and down(cur):
            root = cur
            seen.add(cur.id)
            cur = parent_of.get(cur.id)
        return root

    groups: dict = {}
    for d in devices:
        if down(d):
            r = root_of(d)
            g = groups.setdefault(r.id, Outage(root=r, parent=parent_of.get(r.id)))
            g.affected.append(d)
    outages = sorted(groups.values(), key=lambda o: (-len(o.affected), o.root.name or ""))
    return {"outages": outages, "parent_of": parent_of, "root_of": root_of, "down": down}


def describe_outage(o: Outage) -> str:
    """One plain-English line: what is down and where to look."""
    r, p = o.root, o.parent
    what = r.name or r.model or r.mac or "device"
    behind = len(o.affected) - 1
    tail = f" — {behind} device{'s' if behind != 1 else ''} behind it also down" if behind else ""
    if p is None:
        return f"{what} is offline{tail} (no uplink on record)"
    via = "over wireless mesh" if r.uplink_type == "wireless" else (
        f"on port {r.uplink_port}" if r.uplink_port else "")
    return f"{what} is offline{tail}. Its uplink is {p.name or p.model} {via}".rstrip() + \
        (" — check power at the device and that cable." if r.uplink_type != "wireless"
         else " — check power and line of sight.")
