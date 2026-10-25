"""Automatic IP check (every 10 min, read-only UniFi calls).

Looks for real, LIVE problems only — stale records are ignored:
  * conflicts — two different MACs on the same LAN IP, both seen in the last
                3 minutes (clients) / connected right now (UniFi devices).
                Gateways are skipped (an HA pair shares its WAN IP by design).
  * self-assigned — a connected client on 169.254.x.x (DHCP failed)
  * no IP — a WIRED client connected 15+ min with no IP at all (static IP in
            the wrong subnet, or DHCP not answering)

Results go on Site.ip_issues (dashboard + morning report). A NEW conflict is a
network notice for the RCS-IT group (once per IP per 6 h).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal

logger = logging.getLogger("netmonitor.ipcheck")

INTERVAL_S = 600
LIVE_S = 180
NOIP_AFTER_S = 15 * 60
GATEWAY_TYPES = {"ugw", "uxg", "udm"}
_told: dict[str, datetime] = {}


def analyze(sta: list[dict], dev: list[dict], now_ts: float) -> dict:
    by_ip: dict[str, dict[str, dict]] = defaultdict(dict)
    for x in sta:
        ip, mac = x.get("ip"), (x.get("mac") or "").lower()
        if not ip or not mac or now_ts - (x.get("last_seen") or 0) > LIVE_S:
            continue
        by_ip[ip][mac] = {"mac": mac, "name": x.get("hostname") or x.get("name") or x.get("oui") or mac,
                          "kind": "client", "where": x.get("last_uplink_name") or x.get("essid") or ""}
    for d in dev:
        ip, mac = d.get("ip"), (d.get("mac") or "").lower()
        if not ip or not mac or d.get("state") != 1 or d.get("type") in GATEWAY_TYPES:
            continue
        by_ip[ip][mac] = {"mac": mac, "name": d.get("name") or d.get("model") or mac, "kind": "device", "where": ""}
    conflicts = [{"ip": ip, "entries": list(v.values())} for ip, v in sorted(by_ip.items()) if len(v) > 1]
    apipa = [{"mac": x.get("mac"), "name": x.get("hostname") or x.get("name") or x.get("oui") or x.get("mac"),
              "ip": x.get("ip"), "where": x.get("last_uplink_name") or x.get("essid") or ""}
             for x in sta if (x.get("ip") or "").startswith("169.254.") and now_ts - (x.get("last_seen") or 0) <= LIVE_S]
    no_ip = [{"mac": x.get("mac"), "name": x.get("hostname") or x.get("name") or x.get("oui") or x.get("mac"),
              "network": x.get("network") or "", "where": x.get("last_uplink_name") or "",
              "sw_port": x.get("sw_port"), "minutes": int((x.get("uptime") or 0) / 60)}
             for x in sta if x.get("is_wired") and not x.get("ip") and (x.get("uptime") or 0) >= NOIP_AFTER_S
             and now_ts - (x.get("last_seen") or 0) <= LIVE_S]
    return {"conflicts": conflicts, "apipa": apipa, "no_ip": no_ip,
            "clients": len(sta), "devices": len(dev)}


async def check_once() -> None:
    from app.models import Site
    from app.services.morning import _unifi
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        sites = [s for s in (await db.execute(select(Site))).scalars() if s.console_id and s.unifi_site_ref]
        for s in sites:
            base, h, verify = await _unifi(db, s)
            if not base:
                continue
            try:
                async with httpx.AsyncClient(timeout=30, verify=verify, headers=h) as c:
                    sta = (await c.get(base + "/stat/sta")).json().get("data", [])
                    dev = (await c.get(base + "/stat/device")).json().get("data", [])
            except Exception as exc:  # noqa: BLE001
                logger.info("ip check: %s unreachable: %s", s.name, exc)
                continue
            res = analyze(sta, dev, time.time())
            s.ip_issues, s.ip_issues_at = res, now
            new = [c for c in res["conflicts"]
                   if f"{s.id}:{c['ip']}" not in _told or now - _told[f"{s.id}:{c['ip']}"] > timedelta(hours=6)]
            if new:
                for c in new:
                    _told[f"{s.id}:{c['ip']}"] = now
                lines = [f"• {c['ip']}: " + " vs ".join(f"{e['name']} ({e['mac']})" for e in c["entries"][:3])
                         for c in new[:6]]
                logger.info("IP conflict(s) at %s: %s", s.name, [c["ip"] for c in new])
                try:
                    from app.services import closure
                    from app.services.notify import post_group
                    if closure.phase(now) != "closed":
                        await post_group(db, f"⚠️ IP CONFLICT at {s.name} — two devices using the same address:\n"
                                         + "\n".join(lines) + "\nOne of them needs a different / DHCP address.")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ip conflict notice failed: %s", exc)
        await db.commit()


async def run_ip_check() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await check_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("ip check failed: %s", exc)
        await asyncio.sleep(INTERVAL_S)
