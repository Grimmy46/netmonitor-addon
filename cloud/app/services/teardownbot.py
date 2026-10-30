"""Teardown bot: an extra crew member in the equipment Signal group.

When someone posts what they've grabbed ("#0617", "1081 and 1082", a Zebra
serial "S2433…"), the bot looks each item up in RCS Inventory, marks it
returned (Inventory → In Storage @ ARK, plus the Cases teardown checklist) via
the RCS Teardown Bot API, and replies in the group with the result.

Only runs while switched on in Settings (and before its optional auto-off
time). Practice mode (dry_run) is on until someone turns it off: the bot then
says what *would* change without changing anything.

The API token is stored Fernet-encrypted in signal_config.teardown and is
never returned to the browser.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from app.core.security import decrypt

logger = logging.getLogger("netmonitor.teardownbot")

BASE = "https://yprisdgqxmwvzadaanpk.supabase.co/functions/v1/teardown-bot"
MARK = "📦"  # every bot reply starts with this, so we never answer ourselves
MAX_ITEMS = 12

# "#0617", "0617", "617" as a standalone number (3–4 digits)
_TAG = re.compile(r"(?<![\w.:/-])#?(\d{3,4})(?![\w.:/%-])")
# serials: 8+ alphanumerics containing at least 4 digits (Zebra "S2433…" included)
_SERIAL = re.compile(r"(?<![\w-])([A-Z0-9]{8,24})(?![\w-])", re.I)
# things that look like a number but aren't a tag
_NOT_TAG = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{3,4}\s*(?:mbps|ms|am|pm|ft|w|v)\b", re.I)


def cfg_of(sigcfg) -> dict:
    return dict(sigcfg.teardown or {})


def active(td: dict, now: datetime | None = None) -> bool:
    if not td.get("enabled") or not td.get("group_id") or not td.get("token_enc"):
        return False
    until = td.get("until")
    if until:
        try:
            if (now or datetime.now(timezone.utc)) >= datetime.fromisoformat(until):
                return False
        except ValueError:
            pass
    return True


def extract(text: str) -> list[str]:
    """Candidate asset tags / serials in a message, in order, de-duplicated."""
    if not text or text.startswith(MARK):
        return []
    clean = _NOT_TAG.sub(" ", text)
    out: list[str] = []
    for m in _TAG.finditer(clean):
        if not m.group(0).startswith("#") and 2020 <= int(m.group(1)) <= 2035:
            continue  # a year ("2026 ASF"), unless written as "#2026"
        out.append(m.group(1))
    for m in _SERIAL.finditer(clean):
        s = m.group(1)
        digits = sum(ch.isdigit() for ch in s)
        if (not s.isdigit() and digits >= 4 and not s.isalpha()) or (s.isdigit() and len(s) >= 8):
            out.append(s.upper())
    seen, res = set(), []
    for x in out:
        k = x.lstrip("0") or x
        if k not in seen:
            seen.add(k)
            res.append(x)
    return res[:MAX_ITEMS]


def _token(td: dict) -> str:
    return decrypt(td["token_enc"])


async def lookup(token: str, q: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BASE}/lookup", params={"q": q}, headers={"Authorization": f"Bearer {token}"})
        try:
            return {"http": r.status_code, **r.json()}
        except ValueError:
            return {"http": r.status_code, "ok": False, "error": r.text[:200]}


async def mark_returned(token: str, asset_tag: str, who: str, at: datetime, dry_run: bool, note: str | None,
                        photo: dict | None = None) -> dict:
    body = {"asset_tag": asset_tag, "returned_by": who, "returned_at": at.isoformat(), "dry_run": bool(dry_run)}
    if note:
        body["note"] = note[:300]
    if photo:
        import base64
        body |= {"photo_base64": base64.b64encode(photo["bytes"]).decode(), "photo_mime": photo["mime"],
                 "photo_filename": photo["name"]}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/devices/return", json=body, headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 500:  # API guide: retry once
            r = await c.post(f"{BASE}/devices/return", json=body, headers={"Authorization": f"Bearer {token}"})
        try:
            return {"http": r.status_code, **r.json()}
        except ValueError:
            return {"http": r.status_code, "ok": False, "error": r.text[:200]}


async def health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return (await c.get(f"{BASE}/health")).json().get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def _short(dev: dict) -> str:
    bits = [dev.get("manufacturer"), dev.get("model")]
    where = " / ".join(x for x in (dev.get("venue"), dev.get("area")) if x)
    s = " ".join(b for b in bits if b) or dev.get("category") or "device"
    return f"#{dev.get('asset_tag')} {s}" + (f" ({where})" if where else "")


async def handle(td: dict, body: str, sender: str, sent_at: datetime, attachments: int = 0,
                 photo: dict | None = None) -> str | None:
    """Process one group message; returns the reply text (or None to stay quiet)."""
    items = extract(body)
    who = (td.get("owner") or "Dawid") if sender in ("me", "", "?") else sender
    if not items:
        if attachments:
            return (f"{MARK} NETBOT: I can't read photos yet — please type the 4-digit RCS tag "
                    f"(or the serial) of what you grabbed.")
        return None
    token = _token(td)
    dry = td.get("dry_run", True)
    lines: list[str] = []
    for q in items:
        try:
            lk = await lookup(token, q)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"⚠️ {q}: inventory didn't answer ({type(exc).__name__}) — I'll need you to log it by hand.")
            continue
        if lk.get("http") == 401:
            return f"{MARK} NETBOT: the inventory token was rejected — Dawid, please re-enter it in Settings → Signal."
        res = [r for r in (lk.get("results") or []) if r.get("matched_on") in ("asset_tag", "primary_serial", "secondary_serial", "serial")] \
            or (lk.get("results") or [])
        exact = [r for r in res if r.get("matched_on") != "name"]
        pick = exact if exact else res
        if not pick:
            lines.append(f"❓ {q}: not found in inventory.")
            continue
        if len(pick) > 1:
            opts = ", ".join(_short(r) for r in pick[:4])
            lines.append(f"❓ {q} matches {len(pick)} devices: {opts}. Which one? (reply with the tag)")
            continue
        dev = pick[0]
        try:
            post = " ".join(body.split())[:200]
            rr = await mark_returned(token, dev["asset_tag"], who, sent_at, dry,
                                     note=f'Collected by {who}. Signal post: "{post}"', photo=photo)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"⚠️ {_short(dev)}: couldn't update inventory ({type(exc).__name__}).")
            continue
        if not rr.get("ok"):
            lines.append(f"⚠️ {_short(dev)}: inventory said {rr.get('error') or rr.get('http')}.")
            continue
        if (rr.get("inventory") or {}).get("already_returned"):
            lines.append(f"✅ #{dev['asset_tag']} was already back at ARK — nothing changed.")
            continue
        msg = rr.get("message") or f"#{dev['asset_tag']} returned by {who}."
        lines.append(("🧪 Practice — would log: " if dry else "✅ ") + msg + (" 📷 photo attached." if photo else ""))
    if not lines:
        return None
    head = f"{MARK} NETBOT: got it, {who}." + (" (practice mode — nothing saved)" if dry else "")
    return head + "\n" + "\n".join(lines)
