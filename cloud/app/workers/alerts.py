"""Alert sweep: watches kiosks and the Main site's devices, sends push
notifications for fresh faults, and "back online" notices when they recover.

Policy (agreed with the operator — a traveling carnival, gear powers down
nightly): alert 24/7 but on FAULTS ONLY, with mass-event suppression. When many
devices drop together inside one sweep it's a power-down (or site-wide outage),
so individual pushes are replaced by a single summary and those entities never
get individual recovery pushes either.

Per-entity state machine (devices.alert_state / agents.alert_state):
    None        healthy, or fault not yet handled
    "notified"  down push sent — a recovery push goes out when it returns
    "suppressed" part of a mass event — quiet in both directions
    "stale"     fault was already old when first seen (feature deploy/restart)
Dormant devices (manual or aged-out) never alert. Kiosk stations that were
never claimed never alert.
"""
import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Agent, Device, PushSubscription, Site
from app.services.notify import send_push

logger = logging.getLogger("netmonitor.alerts")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_time(ts: datetime | None) -> str:
    return ts.astimezone(timezone.utc).strftime("%H:%M UTC") if ts else "?"


_PRINTER_FAULTS = ("paper_out", "cover_open", "error")
_PRINTER_ICON = {"paper_out": "🧻", "cover_open": "🔧", "error": "⚠️"}
_PRINTER_BODY = {
    "paper_out": "Ticket printer is OUT OF PAPER — reload the roll",
    "cover_open": "Printer cover / paper door is open",
    "error": "Printer reports an error",
}


def _printer_title(name: str, state: str) -> str:
    return f"{_PRINTER_ICON.get(state, '🖨️')} {name} printer: {state.replace('_', ' ')}"


def _printer_body(a, state: str) -> str:
    return _PRINTER_BODY.get(state, "Printer fault") + (f" · {a.hostname}" if a.hostname else "")


@dataclass
class _Fault:
    """A newly-confirmed fault eligible for a push this sweep."""
    entity: object  # Device | Agent (alert_state gets written)
    title: str
    body: str
    tag: str
    url: str


async def _alert_site_ids(db: AsyncSession) -> set:
    st = get_settings()
    name = st.alert_site_name or st.default_probe_site_name
    rows = (await db.execute(select(Site.id).where(Site.name == name))).scalars()
    return set(rows)


async def sweep(db: AsyncSession) -> dict:
    """One pass. Returns counts (also handy for tests)."""
    st = get_settings()
    now = _now()

    # No ears, no alarms: skip all work until someone has enabled notifications.
    has_subs = (await db.execute(select(PushSubscription.id).limit(1))).first()
    if not has_subs:
        return {"skipped": "no subscriptions"}

    fresh = st.alert_fresh_window_seconds
    faults: list[_Fault] = []
    recoveries: list[_Fault] = []
    printer_faults: list[_Fault] = []  # per-station, never mass-suppressed

    # ── Kiosk agents: claimed stations that stopped checking in ───────────
    agents = (
        await db.execute(select(Agent).where(Agent.machine_id.is_not(None)))
    ).scalars()
    for a in agents:
        seen = _parse_iso(a.last_seen_at)
        if seen is None:
            continue  # claimed but never reported — nothing meaningful to say
        silent_for = (now - seen).total_seconds()
        down = silent_for >= st.alert_kiosk_offline_seconds
        if down and a.alert_state is None:
            fault_age = silent_for - st.alert_kiosk_offline_seconds
            if fault_age <= fresh:
                faults.append(_Fault(
                    entity=a,
                    title=f"🔴 Kiosk {a.name} stopped reporting",
                    body=f"Last check-in {_fmt_time(seen)}"
                    + (f" · {a.hostname}" if a.hostname else ""),
                    tag=f"agent-{a.id}",
                    url="/",
                ))
            else:
                a.alert_state, a.alert_state_at = "stale", now
        elif not down and a.alert_state is not None:
            if a.alert_state == "notified":
                recoveries.append(_Fault(
                    entity=a,
                    title=f"🟢 Kiosk {a.name} is back",
                    body=f"Reporting again as of {_fmt_time(seen)}",
                    tag=f"agent-{a.id}",
                    url="/",
                ))
            a.alert_state, a.alert_state_at = None, None

        # ── Ticket printer: paper out / cover open / error ────────────────
        # Only trust a FRESH reading from an agent that's currently up; a stale
        # status (agent offline / stopped polling) never alerts on its own —
        # the kiosk-offline push already covers that case.
        p_state = a.printer_status
        p_at = _parse_iso(a.printer_status_at)
        p_fresh = p_at is not None and (now - p_at).total_seconds() <= st.alert_printer_fresh_seconds
        if down or p_state is None or p_state == "unknown" or not p_fresh:
            pass  # no trustworthy signal this sweep — leave alert state untouched
        elif p_state in _PRINTER_FAULTS:
            if a.printer_alert_state is None:
                a.printer_alert_state, a.printer_alert_state_at = "pending", now
            elif (
                a.printer_alert_state == "pending"
                and a.printer_alert_state_at is not None
                and (now - a.printer_alert_state_at).total_seconds() >= st.alert_printer_confirm_seconds
            ):
                printer_faults.append(_Fault(
                    entity=a,
                    title=_printer_title(a.name, p_state),
                    body=_printer_body(a, p_state),
                    tag=f"printer-{a.id}", url="/",
                ))
        else:  # "ok" — healthy
            if a.printer_alert_state == "notified":
                recoveries.append(_Fault(
                    entity=a,
                    title=f"🟢 {a.name} printer OK",
                    body="Ticket printer recovered (paper/cover restored)",
                    tag=f"printer-{a.id}", url="/",
                ))
            a.printer_alert_state, a.printer_alert_state_at = None, None

    # ── Devices on the alert site (Main): offline or LAN-unreachable ───────
    site_ids = await _alert_site_ids(db)
    if site_ids:
        devices = (
            await db.execute(select(Device).where(Device.site_id.in_(site_ids)))
        ).scalars()
        dormant_cutoff_secs = st.dormant_after_days * 86400
        probe_fresh = max(600, 3 * st.agent_probe_interval_seconds)
        for d in devices:
            offline_age = (
                (now - d.offline_since).total_seconds() if d.offline_since else None
            )
            dormant = d.manual_dormant or (
                offline_age is not None and offline_age >= dormant_cutoff_secs
            )
            if dormant:
                # Parked/aged-out gear is silent; drop any pending state so a
                # restored device starts clean.
                d.alert_state, d.alert_state_at = None, None
                continue

            kind = None
            fault_since = None
            if d.is_online is False and d.offline_since is not None:
                kind, fault_since = "offline", d.offline_since
            elif (
                d.is_online is True
                and d.local_reachable is False
                and d.local_checked_at is not None
                and (now - d.local_checked_at).total_seconds() < probe_fresh
            ):
                # Kiosk probes are FRESH and say it doesn't answer on the LAN
                # even though UniFi thinks it's up — the orange money signal.
                kind, fault_since = "unreachable", d.local_checked_at

            if kind and d.alert_state is None:
                fault_age = (now - fault_since).total_seconds()
                if fault_age < st.alert_confirm_seconds:
                    continue  # not confirmed yet — maybe next sweep
                if fault_age <= fresh:
                    label = d.name or d.model or "device"
                    if kind == "offline":
                        title = f"🔴 {label} went down"
                        body = f"Offline since {_fmt_time(fault_since)} · Main"
                    else:
                        title = f"🟠 {label} unreachable on the LAN"
                        body = "Up in UniFi but not answering pings · Main"
                    faults.append(_Fault(
                        entity=d, title=title, body=body,
                        tag=f"device-{d.id}", url=f"/#/site/{d.site_id}",
                    ))
                else:
                    d.alert_state, d.alert_state_at = "stale", now
            elif not kind and d.alert_state is not None:
                if d.alert_state == "notified":
                    recoveries.append(_Fault(
                        entity=d,
                        title=f"🟢 {d.name or 'Device'} is back online",
                        body="Recovered · Main",
                        tag=f"device-{d.id}", url=f"/#/site/{d.site_id}",
                    ))
                d.alert_state, d.alert_state_at = None, None

    # ── Whole sites: witnessed online→offline transitions (EVERY site) ─────
    # A site that has never been seen online (packed-up / retired venues stay
    # dark in UniFi for months) can never alert. Site-down pushes are NEVER
    # mass-suppressed — a full site outage is the single loudest thing this
    # system knows how to say.
    site_faults: list[_Fault] = []
    for s in (await db.execute(select(Site))).scalars():
        if s.status == "online":
            if s.alert_state == "notified":
                recoveries.append(_Fault(
                    entity=s,
                    title=f"🟢 Site {s.name} is back online",
                    body="UniFi reports the site up again",
                    tag=f"site-{s.id}", url=f"/#/site/{s.id}",
                ))
            s.alert_state, s.alert_state_at = "ok", now
        elif s.status == "offline":
            if s.alert_state == "ok":
                s.alert_state, s.alert_state_at = "pending", now
            elif (
                s.alert_state == "pending"
                and s.alert_state_at is not None
                and (now - s.alert_state_at).total_seconds() >= st.alert_site_confirm_seconds
            ):
                site_faults.append(_Fault(
                    entity=s,
                    title=f"🔴 SITE DOWN: {s.name}",
                    body=f"Whole site offline since ~{_fmt_time(s.alert_state_at)} — "
                         "WAN or gateway outage",
                    tag=f"site-{s.id}", url=f"/#/site/{s.id}",
                ))
        # degraded/unknown: leave the state alone — degraded is still up,
        # unknown carries no information either way.

    # ── Deliver ────────────────────────────────────────────────────────────
    pushed = 0
    for f in site_faults:
        f.entity.alert_state, f.entity.alert_state_at = "notified", now
        pushed += await send_push(db, {
            "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
        })
    # Printer faults are per-station and always sent (a full-site power-down
    # takes the agents offline, so those printers are skipped above, not here).
    for f in printer_faults:
        f.entity.printer_alert_state, f.entity.printer_alert_state_at = "notified", now
        pushed += await send_push(db, {
            "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
        })
    if len(faults) >= st.alert_mass_threshold:
        # Mass event: one summary instead of a storm. Sounds like a power-down
        # at close — or a site-wide outage, which is exactly one push too.
        for f in faults:
            f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
        names = ", ".join(
            (f.entity.name or "?") for f in faults[:5]
        ) + ("…" if len(faults) > 5 else "")
        pushed += await send_push(db, {
            "title": f"⚡ {len(faults)} things went offline together",
            "body": f"Looks like a power-down or site-wide outage · {names}",
            "tag": "mass-offline",
            "url": "/",
        })
    else:
        for f in faults:
            f.entity.alert_state, f.entity.alert_state_at = "notified", now
            pushed += await send_push(db, {
                "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
            })
    for r in recoveries:
        pushed += await send_push(db, {
            "title": r.title, "body": r.body, "tag": r.tag, "url": r.url,
        })
    await db.commit()
    if faults or site_faults or recoveries or printer_faults:
        logger.info(
            "Alert sweep: %d fault(s), %d site outage(s), %d printer fault(s), "
            "%d recovery(ies), %d push(es) sent",
            len(faults), len(site_faults), len(printer_faults), len(recoveries), pushed,
        )
    return {
        "faults": len(faults),
        "site_faults": len(site_faults),
        "printer_faults": len(printer_faults),
        "recoveries": len(recoveries),
        "pushed": pushed,
    }


async def run_alert_sweeper() -> None:
    interval = get_settings().alert_sweep_interval_seconds
    logger.info("Alert sweeper started (every %ss)", interval)
    while True:
        try:
            async with SessionLocal() as db:
                await sweep(db)
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            logger.warning("Alert sweep failed: %s", exc)
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def alerts_lifespan():
    task = asyncio.create_task(run_alert_sweeper())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
