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
    await _maybe_fire_site_teardowns(db, now)

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

    # ── Teardown suppression (per-entity) ────────────────────────────────────
    # A fault is paused if the global teardown is on OR its site is in teardown —
    # UNLESS the device or its site is flagged keep_monitored (critical: Safety,
    # Main office …), which keeps alerting off the UniFi API even mid-move.
    sites_by_id = {s.id: s for s in (await db.execute(select(Site))).scalars()}

    def _entity_suppressed(entity) -> bool:
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
        if s.keep_monitored:
            return False
        if s.teardown_active:
            return True
        return bool(quiet)

    def _is_suppressed(entity) -> bool:
        return _site_suppressed(entity) if isinstance(entity, Site) else _entity_suppressed(entity)

    # ── Deliver ────────────────────────────────────────────────────────────
    pushed = 0
    suppressed = 0
    for f in site_faults:
        if _site_suppressed(f.entity):
            f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
            suppressed += 1
            continue
        f.entity.alert_state, f.entity.alert_state_at = "notified", now
        pushed += await send_push(db, {
            "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
        })
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
    if len(deliver_faults) >= st.alert_mass_threshold:
        for f in deliver_faults:
            f.entity.alert_state, f.entity.alert_state_at = "suppressed", now
        names = ", ".join(
            (f.entity.name or "?") for f in deliver_faults[:5]
        ) + ("…" if len(deliver_faults) > 5 else "")
        pushed += await send_push(db, {
            "title": f"⚡ {len(deliver_faults)} things went offline together",
            "body": f"Looks like a power-down or site-wide outage · {names}",
            "tag": "mass-offline",
            "url": "/",
        })
    else:
        for f in deliver_faults:
            f.entity.alert_state, f.entity.alert_state_at = "notified", now
            pushed += await send_push(db, {
                "title": f.title, "body": f.body, "tag": f.tag, "url": f.url,
            })
    for r in recoveries:
        if _is_suppressed(r.entity):
            continue  # in teardown — recovered quietly
        pushed += await send_push(db, {
            "title": r.title, "body": r.body, "tag": r.tag, "url": r.url,
        })
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
