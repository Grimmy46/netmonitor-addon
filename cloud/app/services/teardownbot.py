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

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import httpx

from app.core.security import decrypt

logger = logging.getLogger("netmonitor.teardownbot")

# Staging points this at its mock (TEARDOWN_BOT_BASE); prod uses the real API.
BASE = os.environ.get("TEARDOWN_BOT_BASE", "https://yprisdgqxmwvzadaanpk.supabase.co/functions/v1/teardown-bot")
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


async def teardown_running(db) -> bool:
    """The bot only listens in Setup or Teardown mode (or while a site is in
    its own teardown) — never during the live show."""
    return await bot_mode(db) is not None


async def bot_mode(db) -> str | None:
    """"deploy" (Setup), "return" (Teardown) or None (not listening)."""
    from app.services import eventmode
    return await eventmode.bot_mode(db)


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
    clean = re.sub(r"(?<=[A-Za-z])-(?=#?\d)", " - ", clean)        # "ap-0951"
    clean = re.sub(r"(?<=\d)/(?=#?\d{3,4}\b)", " / ", clean)       # "0910/0912"
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


def reply_allowed(mode: str | None) -> bool:
    """Setup-mode replies are posted only when settings.bot_setup_replies is on
    (default off); Teardown replies always go out."""
    if mode == "deploy":
        from app.core.config import get_settings
        return bool(get_settings().bot_setup_replies)
    return True


async def mark_deployed(token: str, asset_tag: str, who: str, at: datetime, venue: str | None,
                        dry_run: bool = True) -> dict:
    """STUB: the RCS Inventory API has no deploy endpoint yet. When it does,
    this should POST {asset_tag, deployed_by, deployed_at, venue, dry_run}
    (mirroring /devices/return). Until then nothing is sent anywhere and the
    bot always answers in practice wording."""
    return {"ok": True, "stub": True, "dry_run": True, "asset_tag": asset_tag}


async def health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return (await c.get(f"{BASE}/health")).json().get("ok") is True
    except Exception:  # noqa: BLE001
        return False


_HINT = re.compile(r"\b(ap|switch|sb|router|asset|tag|teardown|collected|setup|deployed|installed|u7|usw)\b", re.I)


# tags handled since the process started, so an untagged post never re-uses one
_posted: set[str] = set()


async def _match_untagged(token: str, body: str, sent_at: datetime) -> dict:
    """Untagged teardown post → {"tag", "why"} or {"ask"} (see teardown_match)."""
    from app.core.db import SessionLocal
    from app.services import teardown_match as tm

    async def lk(q):
        return await lookup(token, q)

    ask = "I couldn't find a tag in that post — please type the 4-digit RCS tag (or the serial) of what you grabbed."
    try:
        async with SessionLocal() as db:
            r = await tm.resolve(db, body, sent_at, lk, taken=_posted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("untagged match failed: %s", exc)
        return {"ask": ask}
    c = r.get("cand")
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Phoenix")

    def when(x):
        return x.offline_since.astimezone(tz).strftime("%-I:%M %p") if x.offline_since else "?"
    if r["status"] == "match":
        return {"tag": r["tag"], "why": f'UniFi "{c.name}", offline {when(c)}, by {c.via}'}
    if r["status"] == "no_inventory":
        mac = (c.mac or "")[-5:].upper()
        return {"ask": f'that looks like UniFi "{c.name}" (MAC ends {mac}, offline {when(c)}), but it isn\'t in '
                       f"Inventory. Please add it there, or type its 4-digit tag if it has one."}
    if r["status"] == "ambiguous":
        opts = ", ".join(f"#{x.tag} ({x.name})" for x in r["cands"][:3])
        return {"ask": f"no tag in that post — it could be {opts}. Which one? Reply with the tag and a photo."}
    return {"ask": ask}


def _short(dev: dict) -> str:
    bits = [dev.get("manufacturer"), dev.get("model")]
    where = " / ".join(x for x in (dev.get("venue"), dev.get("area")) if x)
    s = " ".join(b for b in bits if b) or dev.get("category") or "device"
    return f"#{dev.get('asset_tag')} {s}" + (f" ({where})" if where else "")


async def _read_photo(td: dict, photo: dict | None) -> dict | None:
    """Tags / QR / UniFi labels read off the photo (local OCR), or None."""
    from app.core.config import get_settings
    if not photo or not photo.get("bytes") or td.get("photo_ocr") is False or not get_settings().photo_ocr:
        return None
    from app.services import photo_reader
    try:
        return await asyncio.wait_for(photo_reader.read_async(photo["bytes"]), timeout=15)
    except Exception as exc:  # noqa: BLE001
        logger.warning("photo read failed: %s", exc)
        return None


async def _match_label(token: str, labels: list[str]) -> dict:
    """UniFi label off the photo → {"tag", "why", "device"} or {"ask"} or {}."""
    from app.core.db import SessionLocal
    from app.services import teardown_match as tm

    async def lk(q):
        return await lookup(token, q)
    try:
        async with SessionLocal() as db:
            r = await tm.resolve_label(db, labels, lk, taken=_posted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("label match failed: %s", exc)
        return {}
    c = r.get("cand")
    if r["status"] == "match":
        return {"tag": r["tag"], "device": c.name,
                "why": f'no tag in the post — read the label "{r["label"]}" off the photo: UniFi "{c.name}", by {c.via}'}
    if r["status"] == "no_inventory":
        mac = (c.mac or "")[-5:].upper()
        return {"device": c.name,
                "ask": f'the photo shows the label "{r["label"]}" — that\'s UniFi "{c.name}" (MAC ends {mac}), but it '
                       f"isn't in Inventory. Please add it there, or type its 4-digit tag if it has one."}
    if r["status"] == "ambiguous":
        opts = ", ".join(f"#{x.tag} ({x.name})" for x in r["cands"][:3])
        return {"ask": f'the label "{r["label"]}" in the photo could be {opts}. Which one? Reply with the tag.'}
    return {}


# A tag read off the photo by OCR (not its QR) is acted on alone only above
# PHOTO_TAG_SURE; below that the bot asks. It counts as contradicting a caption
# tag above PHOTO_TAG_FIRM.
PHOTO_TAG_SURE = 0.93
PHOTO_TAG_FIRM = 0.8


def _norm(t: str) -> str:
    return t.lstrip("0") or t


async def handle(td: dict, body: str, sender: str, sent_at: datetime, attachments: int = 0,
                 photo: dict | None = None, mode: str = "return",
                 venue: str | None = None) -> str | None:
    """Process one group message; returns the reply text (or None to stay quiet).

    mode: "return" (Teardown: mark devices returned) or "deploy" (Setup: mark
    devices deployed at `venue`; practice wording only until Inventory has a
    deploy endpoint — see mark_deployed).

    Where the tag comes from, in order: the caption; the RCS tag (or its QR)
    read off the photo; a UniFi label read off the photo, matched to the
    devices table; the caption matched against devices that just went offline.
    A caption tag that the photo contradicts is not acted on — the bot asks.
    Photos the bot acts on are kept on disk (see bot_photos)."""
    # Teardown posts always carry a photo. Plain chat ("check kiosk 6047 at
    # mini bumper boats") is never treated as a return, even if it has a number.
    if not attachments:
        return None
    read = await _read_photo(td, photo)
    out: dict = {"mode": "deploy" if mode == "deploy" else "return"}
    reply = await _respond(td, body, sender, sent_at, photo, read, out, venue=venue)
    if photo and reply:
        who = (td.get("owner") or "Dawid") if sender in ("me", "", "?") else sender
        await _keep_photo(photo, body, who, sent_at, read, out | {"reply": reply})
    return reply


async def _keep_photo(photo: dict, body: str, who: str, sent_at: datetime, read: dict | None, out: dict) -> None:
    from app.core.db import SessionLocal
    from app.services import bot_photos
    try:
        async with SessionLocal() as db:
            await bot_photos.save(db, photo, caption=body, sender=who, sent_at=sent_at,
                                  mode=out.get("mode") or "return", read=read, outcome=out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not keep teardown photo: %s", exc)


async def _respond(td: dict, body: str, sender: str, sent_at: datetime, photo: dict | None,
                   read: dict | None, out: dict, venue: str | None = None) -> str | None:
    deploy = out.get("mode") == "deploy"
    items = extract(body)
    who = (td.get("owner") or "Dawid") if sender in ("me", "", "?") else sender
    token = _token(td)
    dry = td.get("dry_run", True)
    lines: list[str] = []
    via: dict[str, str] = {}
    ptags = list((read or {}).get("tags") or [])
    tconf = (read or {}).get("tag_conf") or {}
    qr_tags = set()
    if read and read.get("qr"):
        from app.services.photo_reader import qr_tag
        qr_tags = {_norm(t) for t in (qr_tag(q) for q in read["qr"]) if t}
    if items:
        out["via"] = "caption"
        cap_tags = [x for x in items if x.isdigit() and len(x) <= 4]
        firm = [t for t in ptags if tconf.get(t, 1.0) >= PHOTO_TAG_FIRM]
        if firm and cap_tags and not ({_norm(t) for t in cap_tags} & {_norm(t) for t in ptags}):
            out["via"] = "conflict"
            said = ", ".join(f"#{t}" for t in cap_tags)
            seen = ", ".join(f"#{t}" for t in firm)
            return (f"{MARK} NETBOT: the caption says {said}, but the tag in the photo reads {seen}. "
                    f"Which one is it? Reply with the right 4-digit tag and I'll log it.")
    elif ptags:
        unsure = [t for t in ptags if _norm(t) not in qr_tags and tconf.get(t, 1.0) < PHOTO_TAG_SURE]
        if unsure:
            out["via"] = "photo"
            return (f"{MARK} NETBOT: I think the tag in the photo says #{unsure[0]}, but I can't read it well. "
                    f"Please type the 4-digit tag so I log the right one.")
        items = ptags[:MAX_ITEMS]
        out["via"] = "qr" if all(_norm(t) in qr_tags for t in items) else "photo"
        for t in items:
            src = "the QR code" if _norm(t) in qr_tags else "the tag"
            via[t] = f"no tag in the caption — read #{t} off {src} in the photo"
    elif read and read.get("labels"):
        found = await _match_label(token, read["labels"])
        if found.get("device"):
            out["device"] = found["device"]
        if found.get("tag"):
            items = [found["tag"]]
            via[found["tag"]] = found["why"]
            out["via"] = "label"
        elif found.get("ask"):
            out["via"] = "label"
            return f"{MARK} NETBOT: {found['ask']}"
    if not items:
        # Only act when the caption looks like a teardown post; memes and
        # caption-less photos are ignored silently.
        if not _HINT.search(body or ""):
            return None
        if deploy:
            return f"{MARK} NETBOT: I couldn't find a tag in that post — please type the 4-digit RCS tag of what went in."
        found = await _match_untagged(token, body, sent_at)
        if found.get("tag"):
            items = [found["tag"]]
            via[found["tag"]] = f"no tag in the post — matched {found['why']}"
            out["via"] = "offline"
        else:
            return f"{MARK} NETBOT: {found['ask']}"
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
            lines.append(f"❓ {q} matches {len(pick)} devices: {opts}. Which one? (repost the photo with its 4-digit tag)")
            continue
        dev = pick[0]
        out.setdefault("tag", dev.get("asset_tag"))
        if deploy:
            await mark_deployed(token, dev["asset_tag"], who, sent_at, venue)
            _posted.add(dev["asset_tag"])
            at = f" at {venue}" if venue else ""
            lines.append(f"🧪 Practice — would mark #{dev['asset_tag']} deployed{at}."
                         + (f" ({via[q]})" if q in via else "")
                         + (" 📷 photo attached." if photo else ""))
            continue
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
            _posted.add(dev["asset_tag"])
            lines.append(f"✅ #{dev['asset_tag']} was already back at ARK — nothing changed.")
            continue
        _posted.add(dev["asset_tag"])
        msg = rr.get("message") or f"#{dev['asset_tag']} returned by {who}."
        if q in via:
            msg += f" ({via[q]})"
        lines.append(("🧪 Practice — would log: " if dry else "✅ ") + msg + (" 📷 photo attached." if photo else ""))
    if not lines:
        return None
    practice = dry or deploy
    head = f"{MARK} NETBOT: got it, {who}." + (" (practice mode — nothing saved)" if practice else "")
    return head + "\n" + "\n".join(lines)
