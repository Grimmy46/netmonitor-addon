"""Daily access-point restart (default 6:00 AM local, show-route time zone).

Config + state live on Account.ap_restart:
  {enabled, time "06:00", last "YYYY-MM-DD", pending {...}}
At the set time (30-min window, once per local day) every *online* AP on the
main site gets a UniFi soft restart. Alerts for those APs are muted for 20 min
so the reboot never pages anyone. ~15 min later one summary goes to the Signal
alert group (a network notice): how many restarted and whether all came back.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Device

logger = logging.getLogger("netmonitor.ap_restart")

DEFAULTS = {"enabled": True, "time": "06:00"}
WINDOW = timedelta(minutes=30)
MUTE_FOR = timedelta(minutes=20)
CHECK_AFTER = timedelta(minutes=15)


def config(acc: Account | None) -> dict:
    return {**DEFAULTS, **((acc.ap_restart if acc else None) or {})}


def _tz(now: datetime):
    from app.services import route
    return route.tz_now(now)


def due_today(cfg: dict, now: datetime) -> datetime | None:
    try:
        hh, mm = (int(x) for x in str(cfg["time"]).split(":"))
    except Exception:  # noqa: BLE001
        return None
    return now.astimezone(_tz(now)).replace(hour=hh, minute=mm, second=0, microsecond=0)


def next_run(cfg: dict, now: datetime) -> datetime | None:
    due = due_today(cfg, now)
    if due is None:
        return None
    local = now.astimezone(due.tzinfo)
    if cfg.get("last") == local.date().isoformat() or local >= due + WINDOW:
        due += timedelta(days=1)
    return due


async def _aps(db: AsyncSession):
    from app.services.morning import _main_site, _unifi
    site = await _main_site(db)
    if site is None:
        raise RuntimeError("main site not found")
    base, h, verify = await _unifi(db, site)
    if not base:
        raise RuntimeError("no UniFi console for the main site")
    async with httpx.AsyncClient(timeout=45, verify=verify) as c:
        data = (await c.get(f"{base}/stat/device", headers=h)).json().get("data", [])
    return site, base, h, verify, [d for d in data if d.get("type") == "uap"]


def _name(d: dict) -> str:
    return (d.get("name") or d.get("mac") or "?").strip()


async def restart_all(db: AsyncSession, acc: Account, now: datetime) -> dict:
    site, base, h, verify, aps = await _aps(db)
    online = [d for d in aps if d.get("state") == 1 and d.get("mac")]
    macs = {d["mac"].lower() for d in online}
    # Mute first so the reboots never alert (keeps any longer manual mute).
    until = now + MUTE_FOR
    for dev in (await db.execute(select(Device).where(Device.site_id == site.id))).scalars():
        if (dev.mac or "").lower() in macs and not (dev.muted_until and dev.muted_until > until):
            dev.muted_until = until
    await db.commit()
    ok, failed = [], []
    async with httpx.AsyncClient(timeout=20, verify=verify) as c:
        for d in online:
            try:
                r = await c.post(f"{base}/cmd/devmgr", headers=h,
                                 json={"cmd": "restart", "mac": d["mac"].lower(), "reboot_type": "soft"})
                (ok if r.status_code < 300 else failed).append(d)
            except Exception:  # noqa: BLE001
                failed.append(d)
            await asyncio.sleep(0.2)
    logger.info("AP restart: %d restarted, %d failed, %d offline skipped",
                len(ok), len(failed), len(aps) - len(online))
    return {"at": now.isoformat(), "macs": [d["mac"].lower() for d in ok],
            "names": {d["mac"].lower(): _name(d) for d in ok},
            "failed": [_name(d) for d in failed], "skipped": len(aps) - len(online)}


async def _report(db: AsyncSession, pend: dict, now: datetime) -> str:
    _, _, _, _, aps = await _aps(db)
    by = {(d.get("mac") or "").lower(): d for d in aps}
    macs = pend.get("macs") or []
    back = [m for m in macs if (by.get(m) or {}).get("state") == 1]
    missing = sorted(pend["names"].get(m, m) for m in macs if m not in back)
    when = datetime.fromisoformat(pend["at"]).astimezone(_tz(now))
    t = when.strftime("%-I:%M %p")
    lines = [f"🔁 Daily AP restart ({t}): {len(macs)} APs restarted"
             + (f", all {len(back)} back online." if not missing else f", {len(back)} back online.")]
    if missing:
        lines.append(f"⚠️ Not back yet ({len(missing)}): " + ", ".join(missing[:8])
                     + (f" +{len(missing) - 8} more" if len(missing) > 8 else ""))
    if pend.get("failed"):
        lines.append(f"❌ Restart command failed ({len(pend['failed'])}): " + ", ".join(pend["failed"][:8]))
    if pend.get("skipped"):
        lines.append(f"({pend['skipped']} APs were already offline and skipped.)")
    return "\n".join(lines)


async def maybe_run(db: AsyncSession, now: datetime) -> None:
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    if acc is None:
        return
    cfg = config(acc)
    pend = cfg.get("pending")
    if pend and now >= datetime.fromisoformat(pend["at"]) + CHECK_AFTER:
        cfg.pop("pending", None)
        acc.ap_restart = dict(cfg)
        await db.commit()
        try:
            from app.services.morning import send
            await send(db, await _report(db, pend, now))
        except Exception as exc:  # noqa: BLE001
            logger.warning("AP restart report failed: %s", exc)
    if not cfg.get("enabled"):
        return
    due = due_today(cfg, now)
    if due is None:
        return
    local = now.astimezone(due.tzinfo)
    today = local.date().isoformat()
    if cfg.get("last") == today or not (due <= local < due + WINDOW):
        return
    cfg["last"] = today          # claim the day first — never double-restart
    acc.ap_restart = dict(cfg)
    await db.commit()
    try:
        cfg["pending"] = await restart_all(db, acc, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AP restart failed: %s", exc)
        try:
            from app.services.morning import send
            await send(db, f"❌ Daily AP restart didn't run: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return
    acc.ap_restart = dict(cfg)
    await db.commit()
