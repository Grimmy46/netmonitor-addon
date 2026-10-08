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
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import (
    Account,
    Agent,
    Device,
    ProbeSample,
    ProbeTarget,
    PushSubscription,
    Site,
    WanIncident,
)
from app.services import closure
from app.services.names import short, split_name
from app.services.notify import send_push
from app.services.topology import analyze, describe_outage

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


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _fmt_dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{round(seconds / 3600, 1)} h"


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
    # Delivery shaping (see _deliver_faults): switch | ap | device | unreach |
    # kiosk | other; behind = how many devices are down behind this one.
    kind: str = "other"
    behind: int = 0


async def _alert_site_ids(db: AsyncSession) -> set:
    st = get_settings()
    name = st.alert_site_name or st.default_probe_site_name
    rows = (await db.execute(select(Site.id).where(Site.name == name))).scalars()
    return set(rows)


async def _maybe_fire_scheduled_rollout(db: AsyncSession, now: datetime) -> int:
    """If a full exe rollout is armed and its time has passed, flag every claimed
    station for the self-update, post a dashboard notice, and push once. Returns
    the number of stations newly flagged (0 if nothing armed / not yet time)."""
    account = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    if account is None or account.exe_rollout_at is None or now < account.exe_rollout_at:
        return 0
    newly = (await db.execute(
        select(Agent).where(Agent.machine_id.is_not(None), Agent.exe_rollout.is_(False))
    )).scalars().all()
    for a in newly:
        a.exe_rollout = True
    total = int((await db.execute(
        select(func.count()).select_from(Agent).where(Agent.machine_id.is_not(None))
    )).scalar_one())
    account.exe_rollout_at = None  # one-shot: disarm
    account.rollout_notice = (
        f"🚀 Scheduled fleet agent update started — {total} stations flagged; "
        "each updates to the new agent (2.5) within ~10 minutes."
    )
    account.rollout_notice_at = now
    await db.commit()
    logger.info("Scheduled rollout fired: %d newly flagged, %d total claimed", len(newly), total)
    try:
        await send_push(db, {
            "title": "🚀 Fleet agent update started",
            "body": f"{total} kiosks flagged — updating to agent 2.5 over ~10 min.",
            "tag": "rollout", "url": "/",
        })
    except Exception:  # noqa: BLE001 — a push failure must never abort the rollout
        pass
    return len(newly)


def _wan_signal(st, now: datetime, targets, by_target: dict) -> tuple[str, dict]:
    """Classify the WAN health this sweep from the on-lot vantage only.

    Returns (signal, info) where signal is:
      "brownout" — local gateway healthy AND external targets degraded (ISP fault)
      "clear"    — local gateway healthy AND external targets fine
      "unknown"  — on-lot vantage asleep, too few samples, or the gateway itself
                   looks unhealthy (that's a LAN/gateway problem, handled elsewhere)
    info carries peak_loss_pct / peak_latency_ms / worst_target / detail.
    """
    gw = [t for t in targets if (t.target or "").strip().lower() == "gateway"]
    ext = [t for t in targets if (t.target or "").strip().lower() != "gateway"]
    if not gw or not ext:
        return "unknown", {}

    fresh_cut = now - timedelta(seconds=st.live_local_fresh_seconds)
    g_samples: list = []
    for t in gw:
        g_samples += by_target.get(t.id, [])
    g_fresh = any(s.ts >= fresh_cut for s in g_samples)
    if len(g_samples) < st.brownout_min_samples or not g_fresh:
        return "unknown", {}  # kiosk asleep / not enough on-lot data to judge

    g_answered = [s.ms for s in g_samples if s.ms is not None]
    g_loss = 100.0 * (len(g_samples) - len(g_answered)) / len(g_samples)
    g_med = _median(g_answered)
    gateway_healthy = (
        g_loss <= st.brownout_gateway_max_loss_pct
        and g_med is not None
        and g_med <= st.brownout_gateway_max_latency_ms
    )
    if not gateway_healthy:
        return "unknown", {}  # the LAN/gateway itself is bad — not a WAN brownout

    degraded, peak_loss, peak_latency, worst = [], 0.0, 0.0, None
    for t in ext:
        ss = by_target.get(t.id, [])
        if len(ss) < st.brownout_min_samples:
            continue
        answered = [s.ms for s in ss if s.ms is not None]
        loss = 100.0 * (len(ss) - len(answered)) / len(ss)
        mx = max(answered) if answered else 0.0
        if loss >= st.brownout_ext_loss_pct or mx >= st.brownout_ext_latency_ms:
            degraded.append(t)
            if loss > peak_loss or (loss == peak_loss and mx > peak_latency):
                worst = t.label
            peak_loss = max(peak_loss, loss)
            peak_latency = max(peak_latency, mx)

    g_ref = f"gateway {round(g_med)} ms / {round(g_loss)}% loss" if g_med is not None else "gateway ok"
    info = {
        "peak_loss_pct": round(peak_loss, 1),
        "peak_latency_ms": round(peak_latency, 1),
        "worst_target": worst,
        "detail": (
            f"{g_ref}; worst {worst}: {round(peak_loss)}% loss / {round(peak_latency)} ms"
            if worst else g_ref
        ),
    }
    if len(degraded) >= st.brownout_min_degraded_targets:
        return "brownout", info
    return "clear", info


async def _maybe_fire_wan_brownout(db: AsyncSession, now: datetime) -> int:
    """Detect an internet (WAN/ISP) brownout from our own on-lot probes and keep
    an incident log. A brownout = external targets degraded while the local
    gateway is healthy, confirmed over a debounce window. Opens one WanIncident
    per event (pushes an alert), tracks its peak, and closes it (pushes recovery)
    once the internet stays healthy for the clear window. Returns 1 if an incident
    opened this sweep, else 0. Runs regardless of push subscriptions so the log is
    always built."""
    st = get_settings()
    account = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    if account is None:
        return 0
    targets = (await db.execute(
        select(ProbeTarget).where(
            ProbeTarget.account_id == account.id, ProbeTarget.enabled.is_(True)
        )
    )).scalars().all()
    if not targets:
        return 0

    window_start = now - timedelta(seconds=st.brownout_window_seconds)
    rows = (await db.execute(
        select(ProbeSample).where(
            ProbeSample.ts >= window_start,
            ProbeSample.agent_id.is_not(None),  # on-lot vantage only
            ProbeSample.target_id.in_([t.id for t in targets]),
        )
    )).scalars()
    by_target: dict = {}
    for s in rows:
        by_target.setdefault(s.target_id, []).append(s)

    signal, info = _wan_signal(st, now, targets, by_target)

    open_inc = (await db.execute(
        select(WanIncident)
        .where(WanIncident.account_id == account.id, WanIncident.ended_at.is_(None))
        .order_by(WanIncident.started_at.desc())
        .limit(1)
    )).scalars().first()

    opened = 0
    if signal == "brownout":
        if open_inc is not None:
            # Ongoing — extend the peak and cancel any recovery timer.
            open_inc.clearing_since = None
            if info.get("peak_loss_pct") is not None:
                open_inc.peak_loss_pct = max(open_inc.peak_loss_pct or 0.0, info["peak_loss_pct"])
            if info.get("peak_latency_ms") is not None:
                open_inc.peak_latency_ms = max(open_inc.peak_latency_ms or 0.0, info["peak_latency_ms"])
            if info.get("worst_target"):
                open_inc.worst_target = info["worst_target"]
            if info.get("detail"):
                open_inc.detail = info["detail"]
        else:
            if account.brownout_pending_at is None:
                account.brownout_pending_at = now
            elif (now - account.brownout_pending_at).total_seconds() >= st.brownout_confirm_seconds:
                started = account.brownout_pending_at
                inc = WanIncident(
                    account_id=account.id, kind="brownout", started_at=started,
                    peak_loss_pct=info.get("peak_loss_pct"),
                    peak_latency_ms=info.get("peak_latency_ms"),
                    worst_target=info.get("worst_target"), detail=info.get("detail"),
                )
                db.add(inc)
                account.brownout_pending_at = None
                opened = 1
                await db.commit()
                logger.info("WAN brownout opened: %s", info.get("detail"))
                try:
                    await send_push(db, {
                        "title": "🌐 WAN brownout — internet degraded",
                        "body": (
                            f"{info.get('worst_target') or 'External targets'}: "
                            f"{round(info.get('peak_loss_pct') or 0)}% loss / "
                            f"{round(info.get('peak_latency_ms') or 0)} ms while the LAN is fine "
                            "— likely an ISP/Spectrum issue."
                        ),
                        "tag": "wan-brownout", "url": "/",
                    })
                except Exception:  # noqa: BLE001 — push failure must not abort detection
                    pass
                return opened
    elif signal == "clear":
        account.brownout_pending_at = None
        if open_inc is not None:
            if open_inc.clearing_since is None:
                open_inc.clearing_since = now
            elif (now - open_inc.clearing_since).total_seconds() >= st.brownout_clear_seconds:
                open_inc.ended_at = now
                dur = (open_inc.ended_at - open_inc.started_at).total_seconds()
                open_inc.clearing_since = None
                await db.commit()
                logger.info("WAN brownout closed after %s", _fmt_dur(dur))
                try:
                    await send_push(db, {
                        "title": "🟢 WAN recovered",
                        "body": (
                            f"Internet back to normal after {_fmt_dur(dur)} "
                            f"(peak {round(open_inc.peak_loss_pct or 0)}% loss / "
                            f"{round(open_inc.peak_latency_ms or 0)} ms)."
                        ),
                        "tag": "wan-brownout", "url": "/",
                    })
                except Exception:  # noqa: BLE001
                    pass
                return 0
    # signal == "unknown": leave all state untouched (can't tell this sweep).

    await db.commit()
    return opened


async def _maybe_expire_teardown(db: AsyncSession, now: datetime) -> bool:
    """True if teardown mode is currently active (pause all fault alerts). Auto-
    turns it off once its safety expiry passes so it can't silently mask problems
    at the next venue."""
    account = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    if account is None or not account.teardown_mode:
        return False
    if account.teardown_auto_off_at is not None and now >= account.teardown_auto_off_at:
        account.teardown_mode = False
        account.rollout_notice = "🧰 Teardown mode auto-ended (safety expiry) — alerts resumed."
        account.rollout_notice_at = now
        await db.commit()
        logger.info("Teardown mode auto-expired")
        return False
    return True


async def _maybe_fire_site_teardowns(db: AsyncSession, now: datetime) -> None:
    """Arm/expire per-site scheduled teardowns. A site whose one-off scheduled
    time has passed enters teardown (then disarms); an active one whose safety
    auto-off has passed leaves teardown. Critical (keep_monitored) sites never
    enter teardown."""
    changed = False
    for s in (await db.execute(select(Site))).scalars():
        if (s.teardown_scheduled_at is not None and now >= s.teardown_scheduled_at
                and not s.teardown_active and not s.keep_monitored):
            s.teardown_active = True
            s.teardown_since = now
            s.teardown_auto_off_at = s.teardown_auto_off_at or (now + timedelta(hours=18))
            s.teardown_scheduled_at = None  # one-off: disarm
            changed = True
            logger.info("Site teardown activated: %s", s.name)
        elif (s.teardown_active and s.teardown_auto_off_at is not None
              and now >= s.teardown_auto_off_at):
            s.teardown_active = False
            s.teardown_since = None
            s.teardown_auto_off_at = None
            changed = True
            logger.info("Site teardown auto-expired: %s", s.name)
    if changed:
        await db.commit()


def _kind_of(d) -> str:
    t = getattr(d, "device_type", None)
    return "switch" if t in ("switch", "gateway") else "ap" if t == "ap" else "device"


def _lbl(d) -> str:
    label, tag = split_name(getattr(d, "name", None) or getattr(d, "model", None))
    return f"{label} #{tag}" if tag else label


def _where(o) -> str:
    """'Fed from Main office cage, port 22 — check power and that cable.'"""
    r, p = o.root, o.parent
    if p is None:
        return "No uplink on record — check power at the device."
    if r.uplink_type == "wireless":
        return f"Meshes off {_lbl(p)} — check power and line of sight."
    port = f", port {r.uplink_port}" if r.uplink_port else ""
    extra = " (that switch is down too)" if p.is_online is False else ""
    return f"Fed from {_lbl(p)}{port}{extra} — check power and that cable."


def _names(fs, n=4) -> str:
    xs = [_lbl(f.entity) if not isinstance(f.entity, Agent) else (f.entity.name or "kiosk") for f in fs]
    return ", ".join(xs[:n]) + (f" +{len(xs) - n} more" if len(xs) > n else "")


async def _deliver_faults(db: AsyncSession, faults: list, now: datetime) -> int:
    """Shape a sweep's faults into as few, as useful pushes as possible:

    - every feed SWITCH failure is its own high-priority push naming the
      switch, what's behind it and where it plugs in (biggest first; past 3
      at once the rest are folded into one summary)
    - kiosks that drop in the same sweep as a switch are folded into it
      (they're almost certainly behind it) instead of pinging separately
    - APs / other leaf devices are one quiet "AP down" push (batched)
    - LAN-unreachable warnings are batched too
    """
    for f in faults:
        f.entity.alert_state, f.entity.alert_state_at = "notified", now
    sw = sorted([f for f in faults if f.kind == "switch"], key=lambda f: -f.behind)
    aps = [f for f in faults if f.kind in ("ap", "device")]
    unr = [f for f in faults if f.kind == "unreach"]
    kio = [f for f in faults if f.kind == "kiosk"]
    oth = [f for f in faults if f.kind == "other"]
    pushes: list[dict] = []
    crit = {"urgency": "high", "require_interaction": True, "priority": "critical"}

    if sw:
        first = sw[0]
        if kio:
            first.body += f" {len(kio)} kiosk{'s' if len(kio) != 1 else ''} stopped reporting too: {_names(kio, 3)}."
            kio = []
        shown = sw if len(sw) <= 3 else sw[:2]
        for f in shown:
            pushes.append({"title": f.title, "body": f.body, "tag": f.tag, "url": f.url, **crit})
        if len(sw) > 3:
            rest = sw[2:]
            pushes.append({"title": f"🔴 {len(rest)} more switches down",
                           "body": _names(rest, 5) + ". Open the map for where each one plugs in.",
                           "tag": "switches-down", "url": rest[0].url.split("?")[0], **crit})
    if kio:
        if len(kio) == 1:
            f = kio[0]
            pushes.append({"title": f.title, "body": f.body, "tag": f.tag, "url": f.url})
        else:
            pushes.append({"title": f"🔴 {len(kio)} kiosks stopped reporting",
                           "body": _names(kio, 5), "tag": "kiosks-down", "url": "/"})
    if aps:
        if len(aps) == 1:
            f = aps[0]
            pushes.append({"title": f.title, "body": f.body, "tag": f.tag, "url": f.url, "urgency": "normal"})
        else:
            label = "APs" if all(f.kind == "ap" for f in aps) else "devices"
            pushes.append({"title": f"📶 {len(aps)} {label} down", "body": _names(aps, 5) + ".",
                           "tag": "aps-down", "url": aps[0].url.split("?")[0], "urgency": "normal"})
    if unr:
        if len(unr) == 1:
            f = unr[0]
            pushes.append({"title": f.title, "body": f.body, "tag": f.tag, "url": f.url, "urgency": "normal"})
        else:
            pushes.append({"title": f"🟠 {len(unr)} devices not answering on the LAN",
                           "body": "Up in UniFi, not answering kiosk pings: " + _names(unr, 5),
                           "tag": "lan-unreach", "url": unr[0].url.split("?")[0], "urgency": "normal"})
    for f in oth:
        pushes.append({"title": f.title, "body": f.body, "tag": f.tag, "url": f.url})

    sent = 0
    for p in pushes:
        sent += await send_push(db, p)
    return sent


async def _deliver_recoveries(db: AsyncSession, recs: list) -> int:
    if not recs:
        return 0
    if len(recs) <= 2:
        sent = 0
        for r in recs:
            sent += await send_push(db, {"title": r.title, "body": r.body, "tag": r.tag,
                                         "url": r.url, "urgency": "normal"})
        return sent
    return await send_push(db, {"title": f"🟢 {len(recs)} back online", "body": _names(recs, 6) + ".",
                                "tag": "recovered", "url": "/", "urgency": "normal"})


async def _maybe_send_reopen_report(db: AsyncSession, now: datetime) -> None:
    """Once per closure, right after the reopen grace ends: one push listing
    what's still down on Main, grouped by root cause, plus silent kiosks."""
    w = closure.window()
    acc = (await db.execute(select(Account).limit(1))).scalar_one_or_none()
    if w is None or acc is None or acc.closure_report_sent_at is not None or now < w[1]:
        return
    # Don't fire for closures that ended long ago (e.g. right after a deploy).
    if (now - closure.reopen_until()).total_seconds() > 6 * 3600:
        acc.closure_report_sent_at = now
        await db.commit()
        return
    acc.closure_report_sent_at = now
    title, body = await closure_report_text(db, now)
    await db.commit()
    try:
        await send_push(db, {"title": title, "body": body, "tag": "closure-report", "url": "/"})
    except Exception:  # noqa: BLE001
        logger.exception("closure report push failed")
    acc.rollout_notice = f"{title} — {body}"
    acc.rollout_notice_at = now
    await db.commit()


async def closure_report_text(db: AsyncSession, now: datetime) -> tuple[str, str]:
    from app.services import topology as topo
    site_ids = await _alert_site_ids(db)
    devs = list((await db.execute(select(Device).where(Device.site_id.in_(site_ids)))).scalars()) if site_ids else []
    live = [d for d in devs if not topo.is_dormant(d, now)]
    up = sum(1 for d in live if d.is_online is not False)
    res = topo.analyze(live, now)
    roots = res.get("outages", [])
    parts = []
    for o in roots[:3]:
        try:
            parts.append(topo.describe_outage(o))
        except Exception:  # noqa: BLE001
            parts.append(o.root.name or "device")
    kiosks_txt = ""
    try:
        agents = list((await db.execute(select(Agent))).scalars())
        agents = [a for a in agents if a.status != "pending"]
        fresh = [a for a in agents if _parse_iso(a.last_seen_at) and
                 (now - _parse_iso(a.last_seen_at)).total_seconds() <= get_settings().agent_offline_after_seconds]
        if agents:
            kiosks_txt = f" · kiosks {len(fresh)}/{len(agents)} reporting"
    except Exception:  # noqa: BLE001
        pass
    if not roots:
        return "✅ Reopened: everything is back", f"{up}/{len(live)} devices up{kiosks_txt}"
    more = f" (+{len(roots) - 3} more)" if len(roots) > 3 else ""
    return (f"⚠️ Reopened: {len(live) - up} devices didn't come back",
            f"{up}/{len(live)} up{kiosks_txt}. " + " | ".join(parts) + more)


async def _refresh_agent_status(db: AsyncSession, now: datetime) -> None:
    """agents.status used to stay "online" forever; derive it from last_seen."""
    st = get_settings()
    for a in (await db.execute(select(Agent))).scalars():
        seen = _parse_iso(a.last_seen_at)
        if seen is None:
            continue
        want = "online" if (now - seen).total_seconds() <= st.agent_offline_after_seconds else (
            "off" if a.powered_off_at else "offline")
        if a.status != want:
            a.status = want
    await db.commit()


async def _maybe_fire_kiosk_shutdown(db: AsyncSession, now: datetime) -> None:
    """Weekly scheduled power-off of every online kiosk (Account.kiosk_shutdown)."""
    from zoneinfo import ZoneInfo
    acc = (await db.execute(select(Account).limit(1))).scalars().first()
    cfg = (acc.kiosk_shutdown if acc else None) or {}
    if not cfg.get("enabled"):
        return
    try:
        local = now.astimezone(ZoneInfo(cfg.get("tz") or "America/Phoenix"))
        hh, mm = (int(x) for x in str(cfg.get("time", "22:30")).split(":"))
    except Exception:  # noqa: BLE001
        return
    if local.weekday() != int(cfg.get("weekday", 6)):
        return
    due = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    today = local.date().isoformat()
    # Fire once per day, only within 2 h after the set time (no surprise late fire).
    if acc.kiosk_shutdown_last == today or not (due <= local < due + timedelta(hours=2)):
        return
    acc.kiosk_shutdown_last = today
    await db.commit()
    from app.api.routes.agents import queue_fleet
    batch = await queue_fleet(db, "power-off", "schedule", delay=int(cfg.get("delay", 120)),
                              message="NetMonitor: weekly shutdown — save your work")
    logger.info("Weekly kiosk shutdown queued for %s kiosks (batch %s)", batch.total, batch.id)


async def sweep(db: AsyncSession) -> dict:
    """One pass. Returns counts (also handy for tests)."""
    st = get_settings()
    now = _now()

    # Armed scheduled rollout runs regardless of push subscriptions.
    await _maybe_fire_scheduled_rollout(db, now)
    # WAN brownout detection + incident log — also independent of subscriptions.
    await _maybe_fire_wan_brownout(db, now)
    # Teardown: global manual toggle + per-site scheduled teardowns.
    quiet = await _maybe_expire_teardown(db, now)
    # Planned closure: pause alerts through the closure + reopen grace, then
    # send one "what didn't come back" summary.
    await closure.refresh(db)
    # Nightly auto-close (10:30 PM–2 AM, mass kiosk drop → closed till gates open).
    try:
        from app.services import nightly
        await nightly.maybe_autoclose(db, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("nightly auto-close: %s", exc)
    closed = closure.alerts_paused(now)
    if closed:
        quiet = True
    else:
        await _maybe_send_reopen_report(db, now)
    await _maybe_fire_site_teardowns(db, now)
    # Weekly kiosk shutdown (e.g. Sunday after close) + keep agents.status honest.
    await _maybe_fire_kiosk_shutdown(db, now)
    # NETBOT's daily opening report to the Signal group.
    try:
        from app.services import morning
        await morning.maybe_send(db, now)
    except Exception as exc:  # noqa: BLE001 — a report must never break the sweep
        logger.warning("morning report: %s", exc)
    await _refresh_agent_status(db, now)

    # No ears, no alarms: skip all work until someone has enabled notifications.
    has_subs = (await db.execute(select(PushSubscription.id).limit(1))).first()
    if not has_subs:
        return {"skipped": "no subscriptions"}

    fresh = st.alert_fresh_window_seconds
    faults: list[_Fault] = []
    recoveries: list[_Fault] = []
    printer_faults: list[_Fault] = []  # per-station, never mass-suppressed
    paper_low_faults: list[_Fault] = []  # predictive "roll almost out" (own state field)

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
        if down and a.powered_off_at is not None:
            continue  # we shut it down on purpose — not a fault
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
                    kind="kiosk",
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
        elif p_state == "paper_out":   # only paper-out is worth a push
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
            # Recovered: clear quietly (no "printer OK" pushes).
            a.printer_alert_state, a.printer_alert_state_at = None, None

        # ── Predictive paper: the current roll is nearly used up ──────────
        # Only on a fresh reading from a live agent with a known roll anchor.
        if (not down and p_fresh and a.printer_cut_count is not None
                and a.printer_roll_start_cut is not None):
            eff = a.printer_cuts_per_roll or st.paper_seed_cuts_per_roll
            used = max(0, a.printer_cut_count - a.printer_roll_start_cut)
            frac = (used / eff) if eff else 0.0
            if frac >= st.paper_low_pct:
                if a.printer_low_alert_state is None:
                    a.printer_low_alert_state, a.printer_low_alert_at = "pending", now
                elif (
                    a.printer_low_alert_state == "pending"
                    and a.printer_low_alert_at is not None
                    and (now - a.printer_low_alert_at).total_seconds() >= st.paper_low_confirm_seconds
                ):
                    remaining = max(0, int(round(eff - used)))
                    est = "estimate" if a.printer_roll_partial else "learned roll"
                    paper_low_faults.append(_Fault(
                        entity=a,
                        title=f"🧻 {a.name} paper low — swap soon",
                        body=f"~{round(100 * frac)}% of the roll used, ~{remaining} tickets left ({est})",
                        tag=f"paper-{a.id}", url="/",
                    ))
            elif a.printer_low_alert_state is not None:
                # Back under threshold (roll reloaded) — clear quietly.
                a.printer_low_alert_state, a.printer_low_alert_at = None, None

    # ── Devices on the alert site (Main): offline or LAN-unreachable ───────
    site_ids = await _alert_site_ids(db)
    if site_ids:
        devices = (
            await db.execute(select(Device).where(Device.site_id.in_(site_ids)))
        ).scalars().all()
        dormant_cutoff_secs = st.dormant_after_days * 86400
        # Uplink map up front so a fault can wait for its feed switch to settle.
        parent_of0 = analyze(devices, now)["parent_of"]
        probe_fresh = max(600, 3 * st.agent_probe_interval_seconds)
        for d in devices:
            offline_age = (
                (now - d.offline_since).total_seconds() if d.offline_since else None
            )
            dormant = d.manual_dormant or (
                offline_age is not None
                and closure.effective_offline_seconds(d.offline_since, now) >= dormant_cutoff_secs
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
                # Cascade settle: if the switch feeding this device is still
                # "online" but has gone quiet (heartbeat late), it's probably
                # about to be declared down too. Hold a few minutes so the push
                # names the FEED switch instead of its victims first.
                par = parent_of0.get(d.id)
                if (kind == "offline" and par is not None and par.is_online is not False
                        and par.unifi_last_seen is not None
                        and (now - par.unifi_last_seen).total_seconds() > 75
                        and fault_age < st.alert_confirm_seconds + 300):
                    continue
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
                        tag=f"device-{d.id}", url=f"/#/site/{d.site_id}?focus={d.id}",
                        kind=("unreach" if kind == "unreachable" else _kind_of(d)),
                    ))
                else:
                    d.alert_state, d.alert_state_at = "stale", now
            elif not kind and d.alert_state is not None:
                if d.alert_state == "notified":
                    recoveries.append(_Fault(
                        entity=d,
                        title=f"🟢 {short(d.name)} is back online",
                        body="Recovered · Main",
                        tag=f"device-{d.id}", url=f"/#/site/{d.site_id}?focus={d.id}",
                        kind=_kind_of(d),
                    ))
                d.alert_state, d.alert_state_at = None, None

        # ── Root cause: one push per failure point, not one per victim ─────
        # A switch going down takes everything behind it offline too. Group
        # each fresh "offline" fault under the highest offline device above it
        # (from the network map's uplink data): the root gets a single push
        # naming what's behind it and where it plugs in; the rest go quiet
        # (and recover quietly) instead of flooding the phone.
        rc = analyze(devices, now)
        root_of, down_fn = rc["root_of"], rc["down"]
        outage_by_root = {o.root.id: o for o in rc["outages"]}
        grouped: dict = {}
        keep: list[_Fault] = []
        for f in faults:
            d = f.entity
            if isinstance(d, Device) and down_fn(d):
                r = root_of(d)
                if r.id != d.id and r.alert_state in ("notified", "suppressed", "stale"):
                    d.alert_state, d.alert_state_at = "suppressed", now  # root already told
                    continue
                grouped.setdefault(r.id, []).append(f)
            else:
                keep.append(f)
        for rid, fs in grouped.items():
            o = outage_by_root.get(rid)
            root_fault = next((f for f in fs if f.entity.id == rid), None)
            if o is None or (len(o.affected) <= 1 and root_fault is not None):
                if o is not None:
                    for f in fs:         # a lone device: say where it plugs in
                        f.body = _where(o)
                        if f.kind == "switch":
                            f.title = f"🔴 SWITCH DOWN: {_lbl(f.entity)}"
                        elif f.kind == "ap":
                            f.title = f"📶 AP down: {_lbl(f.entity)}"
                keep.extend(fs)
                continue
            for f in fs:
                if f is not root_fault:
                    f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
            root = o.root
            behind = len(o.affected) - 1
            k = _kind_of(root)
            sw_b = sum(1 for x in o.affected if x.id != root.id and x.device_type == "switch")
            ap_b = sum(1 for x in o.affected if x.id != root.id and x.device_type == "ap")
            mix = ", ".join(p for p in [
                f"{sw_b} switch{'es' if sw_b != 1 else ''}" if sw_b else "",
                f"{ap_b} AP{'s' if ap_b != 1 else ''}" if ap_b else "",
                f"{behind - sw_b - ap_b} other" if behind - sw_b - ap_b > 0 else "",
            ] if p)
            keep.append(_Fault(
                entity=root,
                title=(f"🔴 {'GATEWAY' if root.device_type == 'gateway' else 'SWITCH'} DOWN: {_lbl(root)}"
                       if k == "switch" else f"🔴 {_lbl(root)} went down")
                      + (f" (+{behind} behind it)" if behind else ""),
                body=_where(o) + (f" Also offline behind it: {mix}." if mix else ""),
                tag=f"device-{root.id}", url=f"/#/site/{root.site_id}?focus={root.id}",
                kind=k, behind=behind,
            ))
        faults = keep

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

    # ── Teardown suppression (per-entity) ────────────────────────────────────
    # A fault is paused if the global teardown is on OR its site is in teardown —
    # UNLESS the device or its site is flagged keep_monitored (critical: Safety,
    # Main office …), which keeps alerting off the UniFi API even mid-move.
    sites_by_id = {s.id: s for s in (await db.execute(select(Site))).scalars()}

    # A closure ("We're closed") silences EVERYTHING, critical sites included:
    # the whole show is dark, so a down Main switch is expected, not news.
    def _entity_suppressed(entity) -> bool:
        if closed:
            return True
        if getattr(entity, "keep_monitored", False):
            return False
        s = sites_by_id.get(getattr(entity, "site_id", None))
        if s is not None:
            if s.keep_monitored:
                return False
            if s.teardown_active:
                return True
        return bool(quiet)

    def _site_suppressed(s) -> bool:
        if closed:
            return True
        if s.keep_monitored:
            return False
        if s.teardown_active:
            return True
        return bool(quiet)

    def _is_suppressed(entity) -> bool:
        return _site_suppressed(entity) if isinstance(entity, Site) else _entity_suppressed(entity)

    # ── WAN links (WAN1 / WAN2 on the alert site's gateway) ────────────────
    # From the network-map refresher (site.wan_status). A link must read down
    # for alert_wan_confirm_seconds before it pushes; recovery pushes once.
    wan_pushes: list[tuple] = []   # (site, title, body, tag)
    for sid in site_ids:
        s = sites_by_id.get(sid)
        ws = (s.wan_status or {}) if s else {}
        fresh_ws = s is not None and s.wan_status_at is not None and \
            (now - s.wan_status_at).total_seconds() < 5 * st.topology_interval_seconds
        if not fresh_ws:
            continue   # no trustworthy reading — never alert on stale data
        links = ws.get("links") or []
        state = dict(s.wan_alert_state or {})
        for link in links:
            key = link.get("key")
            cur = dict(state.get(key) or {"state": "ok"})
            others_up = [x["key"] for x in links if x.get("key") != key and x.get("up")]
            if not link.get("up"):
                if cur["state"] == "ok":
                    # Remember whether it was carrying traffic before it dropped
                    # (after a failover the gateway flips is_uplink to the survivor).
                    cur = {"state": "pending", "at": now.isoformat(),
                           "was_active": cur.get("was_active", link.get("active"))}
                elif cur["state"] == "pending":
                    since = _parse_iso(cur.get("at")) or now
                    if (now - since).total_seconds() >= st.alert_wan_confirm_seconds:
                        cur["state"] = "notified"
                        if not others_up:
                            body = "NO WAN is up — the site is offline to the internet"
                        elif cur.get("was_active"):
                            body = f"Traffic should fail over to {', '.join(others_up)}"
                        else:
                            body = f"Backup link lost — {', '.join(others_up)} still carrying traffic"
                        wan_pushes.append((s, f"🔴 {s.name} {key} is DOWN", body + f" · {ws.get('gateway') or 'gateway'}",
                                           f"wan-{s.id}-{key}"))
            else:
                if cur["state"] == "notified":
                    wan_pushes.append((s, f"🟢 {s.name} {key} is back up",
                                       f"{key} online again" + (f" · {link.get('latency_ms')} ms" if link.get("latency_ms") is not None else ""),
                                       f"wan-{s.id}-{key}"))
                cur = {"state": "ok", "was_active": bool(link.get("active"))}
            state[key] = cur
        s.wan_alert_state = state

    # ── Deliver ────────────────────────────────────────────────────────────
    pushed = 0
    suppressed = 0
    for s, title, body, tag in wan_pushes:
        if _site_suppressed(s):
            suppressed += 1
            continue
        pushed += await send_push(db, {"title": title, "body": body, "tag": tag,
                                       "url": f"/#/site/{s.id}"})
    for f in site_faults:
        if _site_suppressed(f.entity):
            f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
            suppressed += 1
            continue
        f.entity.alert_state, f.entity.alert_state_at = "notified", now
        pushed += await send_push(db, {
            "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
        })
    # Kiosk pushes paused (settings.alert_kiosks_paused): drop them quietly,
    # marking state so nothing replays when they're turned back on.
    if st.alert_kiosks_paused:
        for f in printer_faults:
            f.entity.printer_alert_state, f.entity.printer_alert_state_at = "suppressed", now
        for f in paper_low_faults:
            f.entity.printer_low_alert_state, f.entity.printer_low_alert_at = "suppressed", now
        for f in faults:
            if isinstance(f.entity, Agent):
                f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
        printer_faults, paper_low_faults = [], []
        faults = [f for f in faults if not isinstance(f.entity, Agent)]
        recoveries = [r for r in recoveries if not isinstance(r.entity, Agent)]
    # Printer faults are per-station and always sent (a full-site power-down
    # takes the agents offline, so those printers are skipped above, not here).
    for f in printer_faults:
        if _entity_suppressed(f.entity):
            f.entity.printer_alert_state, f.entity.printer_alert_state_at = "suppressed", now
            suppressed += 1
            continue
        f.entity.printer_alert_state, f.entity.printer_alert_state_at = "notified", now
        pushed += await send_push(db, {
            "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
        })
    # Low-paper warnings use their own alert-state field.
    for f in paper_low_faults:
        if _entity_suppressed(f.entity):
            f.entity.printer_low_alert_state, f.entity.printer_low_alert_at = "suppressed", now
            suppressed += 1
            continue
        f.entity.printer_low_alert_state, f.entity.printer_low_alert_at = "notified", now
        pushed += await send_push(db, {
            "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
        })
    # Kiosk/device faults: pause the ones in teardown, apply mass-suppression to
    # the rest (so a genuine power-down of NON-teardown gear is still one push).
    for f in [x for x in faults if _entity_suppressed(x.entity)]:
        f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
        suppressed += 1
    deliver_faults = [x for x in faults if not _entity_suppressed(x.entity)]
    pushed += await _deliver_faults(db, deliver_faults, now)
    rec = [r for r in recoveries if not _is_suppressed(r.entity)]
    pushed += await _deliver_recoveries(db, rec)
    await db.commit()
    if faults or site_faults or recoveries or printer_faults or paper_low_faults:
        logger.info(
            "Alert sweep: %d fault(s), %d site outage(s), %d printer fault(s), "
            "%d paper-low, %d recovery(ies), %d push(es) sent, %d teardown-suppressed",
            len(faults), len(site_faults), len(printer_faults),
            len(paper_low_faults), len(recoveries), pushed, suppressed,
        )
    return {
        "faults": len(faults),
        "site_faults": len(site_faults),
        "printer_faults": len(printer_faults),
        "paper_low": len(paper_low_faults),
        "recoveries": len(recoveries),
        "pushed": pushed,
        "suppressed": suppressed,
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
