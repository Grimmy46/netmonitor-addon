"""Teardown bot: find the device behind an untagged teardown post.

Crew often post "AP - Rave Wave" with a photo but no 4-digit tag. We match the
caption against the UniFi devices that went offline shortly before the post
(the crew unplugs, then posts), then turn that device into an Inventory tag:
MAC → Inventory serial first (UniFi names are often stale), then a tag written
in the UniFi name.

Pure read-only: it never changes UniFi or the database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

WINDOW_BEFORE = timedelta(minutes=120)  # unplugged up to 2 h before the post
WINDOW_AFTER = timedelta(minutes=3)     # UniFi can lag the post by a poll or two
WINDOW_LATE = timedelta(hours=12)       # a lone, still-offline name match counts too

_KIND = re.compile(r"\b(ap|access point|switch|sw|sb|switchbox|switch box)\b", re.I)
_MAC = re.compile(r"\b([0-9a-f]{2}[:-][0-9a-f]{2}(?:[:-][0-9a-f]{2}){0,4})\b", re.I)
_NAME_TAG = re.compile(r"(?<![\d.])0\d{3}(?!\d)")
# words that say nothing about *where* a device is
_NOISE = {
    "ap", "aps", "access", "point", "switch", "sw", "sb", "switchbox", "box", "usw", "pro", "max", "poe",
    "u7", "u6", "ac", "mesh", "outdoor", "lite", "lr", "asf", "the", "a", "at", "from", "mac", "cyl",
    "fd", "circ", "24", "16", "60w", "ultra", "mini", "idf", "dist", "aggregation", "xg",
}
_ALIASES = [
    (re.compile(r"\bk\s?(\d{1,2})\b", re.I), r"kiosk \1"),
    (re.compile(r"\btb\s?(\d{1,2})\b", re.I), r"ticket box \1"),
    (re.compile(r"\bdodge\W?m\b|\bdodgem\b", re.I), "dodgem"),
    (re.compile(r"\bgforce\b|\bg-force\b", re.I), "g force"),
    (re.compile(r"\bsearay\b", re.I), "sea ray"),
    (re.compile(r"\bgrandstand\b", re.I), "grand stand"),
]


def _tokens(s: str) -> set[str]:
    s = (s or "").lower()
    for rx, rep in _ALIASES:
        s = rx.sub(rep, s)
    s = _NAME_TAG.sub(" ", s)
    toks = re.findall(r"[a-z]+|\d+", s)
    return {t for t in toks if t not in _NOISE}


def parse_caption(body: str) -> dict:
    """'AP - Wild River MAC (A6:48)' → {kind: 'ap', place: {'wild','river'}, mac: 'a6:48'}"""
    b = body or ""
    km = _KIND.search(b)
    kind = None
    if km:
        kind = "ap" if km.group(1).lower() in ("ap", "access point") else "switch"
    mm = _MAC.search(b)
    mac = mm.group(1).lower().replace("-", ":") if mm else None
    rest = _MAC.sub(" ", b)
    return {"kind": kind, "place": _tokens(rest), "mac": mac}


@dataclass
class Candidate:
    name: str
    mac: str | None
    kind: str | None
    offline_since: datetime | None
    score: float
    in_window: bool
    online: bool | None = None
    name_tag: str | None = None
    tag: str | None = None
    via: str = ""
    notes: list[str] = field(default_factory=list)


def score_device(cap: dict, name: str, mac: str | None) -> float:
    if cap["mac"] and mac and mac.lower().endswith(cap["mac"]):
        return 2.0  # the crew read the MAC off the label
    place = cap["place"]
    if not place:
        return 0.0
    have = _tokens(name)
    hit = len(place & have)
    if hit == 0:
        return 0.0
    # every caption word must appear; numbers ("kiosk 1" vs "kiosk 11") must match exactly
    nums = {t for t in place if t.isdigit()}
    if nums and not nums <= have:
        return 0.0
    return hit / len(place)


async def candidates(db, body: str, sent_at: datetime) -> tuple[dict, list[Candidate]]:
    from app.models import Device
    cap = parse_caption(body)
    if not cap["place"] and not cap["mac"]:
        return cap, []
    q = select(Device.name, Device.mac, Device.device_type, Device.offline_since, Device.is_online)
    if cap["kind"] in ("ap", "switch"):
        q = q.where(Device.device_type == cap["kind"])
    rows = (await db.execute(q)).all()
    out: list[Candidate] = []
    lo, hi = sent_at - WINDOW_BEFORE, sent_at + WINDOW_AFTER
    for name, mac, kind, off, online in rows:
        s = score_device(cap, name or "", mac)
        if s < 1.0:
            continue
        in_win = bool(off and lo <= off <= hi and not online)
        nt = _NAME_TAG.search(name or "")
        out.append(Candidate(name=(name or "").strip(), mac=mac, kind=kind, offline_since=off, score=s,
                             in_window=in_win, online=online, name_tag=nt.group(0) if nt else None))
    # devices that dropped off around the post first, then exact-MAC hits
    out.sort(key=lambda c: (not c.in_window, -c.score, -(c.offline_since.timestamp() if c.offline_since else 0)))
    return cap, out


def _serial_hex(s: str | None) -> str:
    return re.sub(r"[^0-9a-f]", "", (s or "").lower())[:12]


async def resolve(db, body: str, sent_at: datetime, lookup, taken: set[str] | None = None) -> dict:
    """Best guess for an untagged post.

    lookup(q) -> awaitable API /lookup result. Returns
    {"status": "match"|"no_inventory"|"ambiguous"|"none", "tag", "cand", "others"}.
    """
    taken = taken or set()
    cap, cands = await candidates(db, body, sent_at)
    live = [c for c in cands if c.in_window] or [c for c in cands if c.score >= 2.0]
    if not live:
        late = [c for c in cands if c.online is False and c.offline_since
                and sent_at - WINDOW_LATE <= c.offline_since <= sent_at + WINDOW_AFTER]
        if len(late) == 1:
            live = late
    if not live:
        return {"status": "none", "cap": cap, "others": cands[:3]}
    resolved: list[Candidate] = []
    for c in live[:4]:
        # 1) MAC → Inventory serial (beats a stale tag in the UniFi name)
        if c.mac:
            lk = await lookup(c.mac)
            hits = [r for r in (lk.get("results") or []) if r.get("matched_on") != "name"
                    and _serial_hex(r.get("primary_serial")) == _serial_hex(c.mac)]
            if len(hits) == 1:
                c.tag, c.via = hits[0]["asset_tag"], "MAC"
        # 2) tag written in the UniFi name, if Inventory doesn't tie it to a different MAC
        if not c.tag and c.name_tag:
            lk = await lookup(c.name_tag)
            hits = [r for r in (lk.get("results") or []) if r.get("matched_on") == "asset_tag"]
            if len(hits) == 1:
                ser = _serial_hex(hits[0].get("primary_serial"))
                if ser and c.mac and ser != _serial_hex(c.mac):
                    c.notes.append(f"Inventory #{c.name_tag} has a different MAC")
                else:
                    c.tag, c.via = hits[0]["asset_tag"], "UniFi name"
        if c.tag and c.tag.lstrip("0") in {t.lstrip("0") for t in taken}:
            c.notes.append(f"#{c.tag} was already posted")
            c.tag = None
            continue
        resolved.append(c)
    tagged = [c for c in resolved if c.tag]
    if len({c.tag for c in tagged}) == 1:
        return {"status": "match", "tag": tagged[0].tag, "cand": tagged[0], "cap": cap}
    if len(tagged) > 1:
        return {"status": "ambiguous", "cands": tagged, "cap": cap}
    if resolved:
        return {"status": "no_inventory", "cand": resolved[0], "cap": cap}
    return {"status": "none", "cap": cap, "others": live[:3]}
