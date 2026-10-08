"""The show route: which fair we're at on a given date and when its gates open.

Sources (fetched 2026-10-08; update when the fairs publish new hours):
- Arizona State Fair  https://azstatefair.com/arizona-state-fair-hours/
    2026: Oct 1 – Nov 1, Thu–Sun, gates 12 PM.
- Houston Livestock Show & Rodeo (Midway carnival)
    https://www.rodeohouston.com/plan-your-visit/carnival/
    2026 per-day table (Mar 2–22); 2027: Mar 2–21 (hours not out yet — the
    2026 pattern is used: opening day 2 PM, weekdays 12 PM, weekends 10 AM).
- Pima County Fair  https://pimacountyfair.com/fair/
    Mon–Fri 3 PM, Sat–Sun 11 AM. 2027: Apr 15–25.
- LA County Fair  https://www.lacountyfair.com/  (hours: Thu–Sun 11 AM, plus
    Memorial Day). 2027: May 6–31.
- OC Fair  https://ocfair.com/  Wed–Sun 11 AM, closed Mon & Tue.
    2026: Jul 17 – Aug 16 (2027 dates not published yet).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# weekday(): Mon=0 … Sun=6.  None = fair closed that day.
_WEEK_ASF = {3: "12:00", 4: "12:00", 5: "12:00", 6: "12:00"}
_WEEK_HLSR = {0: "12:00", 1: "12:00", 2: "12:00", 3: "12:00", 4: "12:00", 5: "10:00", 6: "10:00"}
_WEEK_PIMA = {0: "15:00", 1: "15:00", 2: "15:00", 3: "15:00", 4: "15:00", 5: "11:00", 6: "11:00"}
_WEEK_LACF = {3: "11:00", 4: "11:00", 5: "11:00", 6: "11:00"}
_WEEK_OC = {2: "11:00", 3: "11:00", 4: "11:00", 5: "11:00", 6: "11:00"}

_HLSR_2026 = {  # Midway carnival, exact per-day table
    "2026-03-02": "14:00", "2026-03-03": "16:00", "2026-03-04": "13:00", "2026-03-05": "11:00",
    "2026-03-06": "16:00", "2026-03-07": "10:00", "2026-03-08": "10:00", "2026-03-09": "12:00",
    "2026-03-10": "12:00", "2026-03-11": "10:00", "2026-03-12": "12:00", "2026-03-13": "12:00",
    "2026-03-14": "10:00", "2026-03-15": "10:00", "2026-03-16": "12:00", "2026-03-17": "12:00",
    "2026-03-18": "12:00", "2026-03-19": "12:00", "2026-03-20": "12:00", "2026-03-21": "10:00",
    "2026-03-22": "10:00",
}

EVENTS = [
    {"key": "hlsr", "name": "Houston Livestock Show and Rodeo", "tz": "America/Chicago", "week": _WEEK_HLSR,
     "runs": [("2026-03-02", "2026-03-22", _HLSR_2026), ("2027-03-02", "2027-03-21", {"2027-03-02": "14:00"})]},
    {"key": "pima", "name": "Pima County Fair", "tz": "America/Phoenix", "week": _WEEK_PIMA,
     "runs": [("2026-04-16", "2026-04-26", {}), ("2027-04-15", "2027-04-25", {})]},
    {"key": "lacf", "name": "LA County Fair", "tz": "America/Los_Angeles", "week": _WEEK_LACF,
     "runs": [("2026-05-07", "2026-05-31", {"2026-05-25": "11:00"}),
              ("2027-05-06", "2027-05-31", {"2027-05-31": "11:00"})]},
    {"key": "oc", "name": "OC Fair", "tz": "America/Los_Angeles", "week": _WEEK_OC,
     "runs": [("2026-07-17", "2026-08-16", {})]},
    {"key": "asf", "name": "Arizona State Fair", "tz": "America/Phoenix", "week": _WEEK_ASF,
     "runs": [("2026-10-01", "2026-11-01", {})]},
]

DEFAULT_TZ = "America/Phoenix"


def _d(s: str) -> date:
    return date.fromisoformat(s)


def event_on(day: date) -> tuple[dict, dict] | None:
    """(event, run-overrides) for the fair running on `day`, else None."""
    for ev in EVENTS:
        for start, end, over in ev["runs"]:
            if _d(start) <= day <= _d(end):
                return ev, over
    return None


def current_event(now: datetime) -> dict | None:
    """The fair we're at (or about to open / just closed, ±3 days)."""
    for off in (0, -1, 1, -2, 2, -3, 3):
        for ev in EVENTS:
            day = now.astimezone(ZoneInfo(ev["tz"])).date() + timedelta(days=off)
            hit = event_on(day)
            if hit and hit[0] is ev:
                return ev
    return None


def tz_now(now: datetime) -> ZoneInfo:
    ev = current_event(now)
    return ZoneInfo(ev["tz"] if ev else DEFAULT_TZ)


def opening_on(day: date) -> datetime | None:
    """Gate-open time (tz-aware) on `day`, or None if no fair / closed that day."""
    hit = event_on(day)
    if not hit:
        return None
    ev, over = hit
    hhmm = over.get(day.isoformat()) or ev["week"].get(day.weekday())
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=ZoneInfo(ev["tz"]))


def next_opening(now: datetime, within_days: int = 14) -> datetime | None:
    """The next gate opening strictly after `now`."""
    tz = tz_now(now)
    d0 = now.astimezone(tz).date()
    for i in range(within_days + 1):
        o = opening_on(d0 + timedelta(days=i))
        if o and o > now:
            return o
    return None


def today_info(now: datetime) -> dict:
    tz = tz_now(now)
    today = now.astimezone(tz).date()
    ev = current_event(now)
    o = opening_on(today)
    nxt = next_opening(now)
    return {"event": ev["name"] if ev else None, "tz": str(tz),
            "opens_today": o.isoformat() if o else None,
            "next_opening": nxt.isoformat() if nxt else None}
