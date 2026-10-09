"""Ticket-paper counting and roll-change log.

Inputs per printer poll (from the agent): lifetime cut count (GS E2 ≈ tickets),
lifetime printed paper in cm (GS E3), the near-end sensor (DLE EOT 20) and the
status state. Outputs: a per-day ticket/cm total, one PrinterRoll row per REAL
roll change, and the current-roll gauge.

A roll change is logged when:
  ran_out  — the printer sat in paper_out (possibly via cover_open) for ≥ 60 s
             and then came back ok (blips shorter than that are sensor noise)
  swapped  — the paper door was open ≥ 30 s (no run-out) and closed with ≥ 30%
             of the roll used (fresh roll put in before it ran out)
  manual   — someone pressed "New roll" in the dashboard
  inferred — far more paper used than a roll holds (a change we missed)
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Agent, PrinterDaily, PrinterEvent, PrinterRoll

SHOW_TZ = ZoneInfo("America/Los_Angeles")
MIN_OUT_S = 60
MIN_OPEN_S = 30


def seed_roll_cm() -> float:
    return float(get_settings().paper_roll_length_ft) * 30.48


def roll_usage(a: Agent) -> dict | None:
    """Current roll: fraction used, tickets left, roll size, estimate flag."""
    if a.printer_paper_cm and a.printer_roll_start_cm is not None and a.printer_paper_cm > 0:
        roll = a.printer_cm_per_roll or seed_roll_cm()
        used = max(0, a.printer_paper_cm - a.printer_roll_start_cm)
        per_ticket = (a.printer_paper_cm / a.printer_cut_count) if a.printer_cut_count else None
        left_cm = max(0.0, roll - used)
        return {
            "frac": (used / roll) if roll else 0.0,
            "used_cm": used,
            "roll_cm": roll,
            "tickets_left": int(left_cm / per_ticket) if per_ticket else None,
            "tickets_this_roll": (max(0, a.printer_cut_count - a.printer_roll_start_cut)
                                  if a.printer_cut_count and a.printer_roll_start_cut is not None else None),
            "learned": a.printer_cm_per_roll is not None,
            "partial": bool(a.printer_roll_partial),
        }
    if a.printer_cut_count and a.printer_roll_start_cut is not None and a.printer_cut_count > 0:
        eff = a.printer_cuts_per_roll or get_settings().paper_seed_cuts_per_roll
        used = max(0, a.printer_cut_count - a.printer_roll_start_cut)
        return {"frac": (used / eff) if eff else 0.0, "used_cm": None, "roll_cm": None,
                "tickets_left": max(0, int(round(eff - used))), "tickets_this_roll": used,
                "learned": a.printer_cuts_per_roll is not None, "partial": bool(a.printer_roll_partial)}
    return None


def _anchor(a: Agent, now: datetime, partial: bool) -> None:
    if a.printer_cut_count:
        a.printer_roll_start_cut = a.printer_cut_count
    if a.printer_paper_cm:
        a.printer_roll_start_cm = a.printer_paper_cm
    a.printer_roll_start_at = now
    a.printer_roll_partial = partial
    a.printer_low_alert_state = a.printer_low_alert_at = None


async def log_change(db: AsyncSession, a: Agent, how: str, now: datetime,
                     out_seconds: int | None = None) -> PrinterRoll:
    """Close the current roll, learn its size if it ran out cleanly, start a new one."""
    tickets = (a.printer_cut_count - a.printer_roll_start_cut
               if a.printer_cut_count and a.printer_roll_start_cut is not None else None)
    cm = (a.printer_paper_cm - a.printer_roll_start_cm
          if a.printer_paper_cm and a.printer_roll_start_cm is not None else None)
    row = PrinterRoll(account_id=a.account_id, agent_id=a.id, how=how, out_seconds=out_seconds,
                      prev_roll_tickets=tickets, prev_roll_cm=cm,
                      prev_roll_partial=bool(a.printer_roll_partial),
                      cut_count=a.printer_cut_count, paper_cm=a.printer_paper_cm)
    db.add(row)
    st = get_settings()
    if how == "ran_out" and not a.printer_roll_partial:
        if cm and cm >= 0.4 * seed_roll_cm():
            cur = a.printer_cm_per_roll
            a.printer_cm_per_roll = float(cm) if cur is None else round(0.5 * cur + 0.5 * cm, 1)
        if tickets and tickets >= st.paper_min_learn_cuts:
            cur = a.printer_cuts_per_roll
            a.printer_cuts_per_roll = float(tickets) if cur is None else round(0.5 * cur + 0.5 * tickets, 1)
    _anchor(a, now, partial=(how == "inferred"))
    # The next near-end reading must not look like another swap.
    a.printer_near_end, a.printer_near_end_since = None, None
    return row


async def _daily(db: AsyncSession, a: Agent, now: datetime) -> None:
    day = now.astimezone(SHOW_TZ).date()
    cuts, cm = a.printer_cut_count, a.printer_paper_cm
    prev = (await db.execute(
        select(PrinterDaily.last_cuts, PrinterDaily.last_cm)
        .where(PrinterDaily.agent_id == a.id, PrinterDaily.day < day)
        .order_by(PrinterDaily.day.desc()).limit(1)
    )).first()
    stmt = pg_insert(PrinterDaily).values(
        agent_id=a.id, day=day, first_cuts=cuts, last_cuts=cuts, first_cm=cm, last_cm=cm,
        prev_last_cuts=prev[0] if prev else None, prev_last_cm=prev[1] if prev else None,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PrinterDaily.agent_id, PrinterDaily.day],
        set_={"last_cuts": cuts, "last_cm": cm, "updated_at": now},
    )
    await db.execute(stmt)


async def track(db: AsyncSession, a: Agent, cut_count: int | None, paper_cm: int | None,
                near_end: bool | None, prev: str | None, new_state: str | None, now: datetime) -> None:
    st = get_settings()
    got_counter = False
    if cut_count and cut_count > 0 and cut_count >= (a.printer_cut_count or 0):
        a.printer_cut_count, a.printer_cut_count_at = cut_count, now
        got_counter = True
    if paper_cm and paper_cm > 0 and paper_cm >= (a.printer_paper_cm or 0):
        a.printer_paper_cm = paper_cm
        got_counter = True
    # First real sighting (or a stale 0 anchor from the old broken counter):
    # anchor mid-roll — an estimate until the next real change.
    if a.printer_cut_count and (a.printer_roll_start_cut in (None, 0) or a.printer_roll_start_cut > a.printer_cut_count):
        _anchor(a, now, partial=True)
    if a.printer_paper_cm and (a.printer_roll_start_cm is None or a.printer_roll_start_cm > a.printer_paper_cm):
        a.printer_roll_start_cm = a.printer_paper_cm
        a.printer_roll_partial = True

    changed = False
    # Run-out → reload.
    # (stays set through cover_open/error while the roll is being swapped)
    if new_state == "paper_out" and a.printer_out_since is None:
        a.printer_out_since = now
    if new_state == "ok" and a.printer_out_since is not None:
        out_s = int((now - a.printer_out_since).total_seconds())
        a.printer_out_since = None
        if out_s >= MIN_OUT_S:
            await log_change(db, a, "ran_out", now, out_seconds=out_s)
            changed = True
    # Swapped before empty: the paper door was opened ≥ 30 s (no run-out) and
    # closed again with a real part of the roll used. (The KPM180H near-end
    # bit reads "on" on nearly every kiosk — no sensor fitted — so it's only
    # stored, never trusted.)
    if not changed and prev == "cover_open" and new_state == "ok":
        opened = (await db.execute(
            select(PrinterEvent.created_at).where(
                PrinterEvent.agent_id == a.id, PrinterEvent.state == "cover_open")
            .order_by(PrinterEvent.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        u = roll_usage(a)
        if opened and (now - opened).total_seconds() >= MIN_OPEN_S and (u is None or u["frac"] >= 0.3):
            await log_change(db, a, "swapped", now, out_seconds=int((now - opened).total_seconds()))
            changed = True
    if near_end is not None and not changed:
        a.printer_near_end = near_end
    # Missed change: used far more than a roll holds.
    if not changed:
        u = roll_usage(a)
        if u and u["frac"] > st.paper_overrun_factor:
            await log_change(db, a, "inferred", now)
    if got_counter:
        await _daily(db, a, now)
