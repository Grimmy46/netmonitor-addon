"""NETBOT's daily opening report to the Signal alert group (RCS-IT).

Config lives on Account.morning_report:
  {enabled, time "12:05", tz "America/Phoenix",
   speedtest: bool (run a UniFi gateway speed test ~10 min before),
   min_down, min_up (Mbps), max_latency (ms)}
Sent once per local day, within 2 h after the set time, and never while the
show is closed (planned closure / "We're closed").
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt
from app.models import Account, Agent, Site, UnifiConsole
from app.services import closure

logger = logging.getLogger("netmonitor.morning")

DEFAULTS = {"enabled": True, "time": "12:05", "tz": "America/Phoenix", "speedtest": False,
            "min_down": 100, "min_up": 20, "max_latency": 80}
_STALE_DAYS = 7
_speedtest_fired: dict[str, str] = {}  # local date → "fired"


def config(acc: Account | None) -> dict:
    return {**DEFAULTS, **((acc.morning_report if acc else None) or {})}


def _iso(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def _main_site(db: AsyncSession) -> Site | None:
    st = get_settings()
    name = st.alert_site_name or st.default_probe_site_name
    return (await db.execute(select(Site).where(Site.name == name))).scalars().first()


def _names(xs: list[str], n: int = 5) -> str:
    xs = sorted(xs, key=lambda s: (len(s), s))
    return ", ".join(xs[:n]) + (f" +{len(xs) - n} more" if len(xs) > n else "")


async def build(db: AsyncSession, now: datetime | None = None) -> str:
    """The report text (also used for the Preview button)."""
    from app.api.routes.network import _site_view
    now = now or datetime.now(timezone.utc)
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    cfg = config(acc)
    try:
        local = now.astimezone(ZoneInfo(cfg["tz"]))
    except Exception:  # noqa: BLE001
        local = now
    issues = 0
    lines: list[str] = []

    # Kiosks (retired/stale ones — silent 7+ days — don't count).
    kiosks = [a for a in (await db.execute(select(Agent).where(Agent.station_group == "kiosk"))).scalars()
              if a.claimed_at and (_iso(a.last_seen_at) or now - timedelta(days=99)) > now - timedelta(days=_STALE_DAYS)]
    up = [a for a in kiosks if a.status == "online"]
    down = [a.name for a in kiosks if a.status != "online"]
    mark = "✅" if not down else "⚠️"
    issues += bool(down)
    lines.append(f"{mark} Kiosks: {len(up)}/{len(kiosks)} online" + (f" — offline: {_names(down)}" if down else ""))

    # Ticket printers (only kiosks that are up and have a fresh reading).
    bad = [a.name for a in up if a.printer_status in ("paper_out", "cover_open", "error")]
    if up:
        mark = "✅" if not bad else "⚠️"
        issues += bool(bad)
        okp = sum(1 for a in up if a.printer_status == "ok")
        lines.append(f"{mark} Printers: {okp}/{len(up)} OK" + (f" — check: {_names(bad)}" if bad else ""))

    site = await _main_site(db)
    view = await _site_view(db, site) if site else None
    if view:
        for kind, label in (("switch", "Switches"), ("ap", "APs")):
            c = view["counts"][kind]
            downs = [n["name"] or "?" for n in view["nodes"]
                     if n["type"] == kind and not n["dormant"] and not n["is_online"]]
            ok = c["online"] == c["total"]
            issues += not ok
            lines.append(f"{'✅' if ok else '⚠️'} {label}: {c['online']}/{c['total']} online"
                         + (f" — down: {_names(downs)}" if downs else ""))

        wan = view.get("wan") or {}
        links = wan.get("links") or []
        speed = await _speedtest_result(db, site) if cfg.get("speedtest") else None
        for link in links:
            n = str(link.get("key") or "WAN1").replace("WAN", "WAN ")
            upl = bool(link.get("up"))
            lat = link.get("latency_ms")
            avail = link.get("availability")
            parts = ["up" if upl else "DOWN"]
            if link.get("active"):
                parts.append("carrying traffic")
            if lat is not None:
                parts.append(f"{lat} ms")
            if avail is not None:
                parts.append(f"{round(float(avail))}% uptime")
            good = upl and (lat is None or lat <= cfg["max_latency"])
            line = f"{'✅' if good else '⚠️'} {n}: " + " · ".join(parts)
            if speed and link.get("active"):
                d, u = speed["down"], speed["up"]
                sp_ok = d >= cfg["min_down"] and u >= cfg["min_up"]
                good = good and sp_ok
                line = f"{'✅' if good else '⚠️'} {n}: " + " · ".join(parts)
                line += (f"\n     Speed test ↓ {d:.0f} / ↑ {u:.0f} Mbps "
                         f"({'within' if sp_ok else 'BELOW'} target ↓ {cfg['min_down']} / ↑ {cfg['min_up']})")
            issues += not good
            lines.append(line)
        if wan.get("stale"):
            lines.append("⚠️ WAN reading is stale — UniFi hasn't reported in a while")
            issues += 1

    head = (f"☀️ Good morning IT team — NETBOT here with the opening check "
            f"({local.strftime('%a %b %-d, %-I:%M %p')})")
    tail = ("All systems go. Have a great show! 🎡" if not issues
            else f"{issues} item{'s' if issues != 1 else ''} need a look before the gates open.")
    return "\n".join([head, "", *lines, "", tail])


# ── Optional gateway speed test (UniFi runs it on the active WAN) ──────────
async def _unifi(db: AsyncSession, site: Site):
    console = await db.get(UnifiConsole, site.console_id) if site.console_id else None
    if console is None:
        return None, None, None
    from urllib.parse import urlparse
    p = urlparse(console.base_url)
    base = f"{p.scheme}://{p.netloc}/proxy/network/api/s/{site.unifi_site_ref}"
    return base, {"X-API-KEY": decrypt(console.encrypted_api_key)}, console.verify_tls


async def _start_speedtest(db: AsyncSession, site: Site) -> None:
    base, h, verify = await _unifi(db, site)
    if not base:
        return
    async with httpx.AsyncClient(timeout=30, verify=verify) as c:
        r = await c.post(f"{base}/cmd/devmgr", headers=h, json={"cmd": "speedtest"})
        logger.info("Morning speed test started: %s", r.status_code)


async def _speedtest_result(db: AsyncSession, site: Site) -> dict | None:
    base, h, verify = await _unifi(db, site)
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, verify=verify) as c:
            data = (await c.get(f"{base}/stat/device", headers=h)).json().get("data", [])
    except Exception:  # noqa: BLE001
        return None
    best = None
    for g in data:
        stt = g.get("speedtest-status") or {}
        if stt.get("rundate") and (best is None or stt["rundate"] > best["rundate"]):
            best = stt
    if not best or datetime.now().timestamp() - best["rundate"] > 3 * 3600:
        return None  # no fresh result
    return {"down": float(best.get("xput_download") or 0), "up": float(best.get("xput_upload") or 0),
            "ping": best.get("latency")}


async def maybe_send(db: AsyncSession, now: datetime) -> None:
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    cfg = config(acc)
    if acc is None or not cfg.get("enabled") or closure.alerts_paused(now):
        return
    try:
        local = now.astimezone(ZoneInfo(cfg["tz"]))
        hh, mm = (int(x) for x in str(cfg["time"]).split(":"))
    except Exception:  # noqa: BLE001
        return
    due = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    today = local.date().isoformat()
    if cfg.get("speedtest") and _speedtest_fired.get("d") != today and \
            due - timedelta(minutes=10) <= local < due - timedelta(minutes=2):
        _speedtest_fired["d"] = today
        site = await _main_site(db)
        if site:
            try:
                await _start_speedtest(db, site)
            except Exception as exc:  # noqa: BLE001
                logger.warning("speed test start failed: %s", exc)
    if acc.morning_report_last == today or not (due <= local < due + timedelta(hours=2)):
        return
    acc.morning_report_last = today
    await db.commit()
    try:
        await send(db, await build(db, now))
    except Exception as exc:  # noqa: BLE001
        logger.warning("morning report failed: %s", exc)


async def send(db: AsyncSession, text: str) -> None:
    from app.services import signal as sig
    cfg = await sig.get_config(db)
    if not cfg.alert_group_id or not cfg.number:
        raise RuntimeError("No Signal alert group set")
    await sig.send_group(cfg.number, cfg.alert_group_id, text)
