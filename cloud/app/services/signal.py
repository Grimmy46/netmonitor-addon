"""Signal bot plumbing: talk to the signal-cli REST container, archive the
watched group chats, import exported history, mine the equipment list.

The bot is a linked device on the user's own account, so it sees every chat
they're in — we only *store* the groups they tick in Settings."""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Device, SignalConfig, SignalMessage

logger = logging.getLogger("netmonitor.signal")


def _url(path: str) -> str:
    return get_settings().signal_api_url.rstrip("/") + path


async def api_get(path: str, timeout: float = 20, **params):
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(_url(path), params=params or None)
        r.raise_for_status()
        return r


async def available() -> bool:
    try:
        await api_get("/v1/about", timeout=4)
        return True
    except Exception:  # noqa: BLE001
        return False


async def get_config(db: AsyncSession) -> SignalConfig:
    cfg = (await db.execute(select(SignalConfig).limit(1))).scalars().first()
    if cfg is None:
        cfg = SignalConfig(watched={})
        db.add(cfg)
        await db.flush()
    return cfg


async def linked_number(db: AsyncSession) -> str | None:
    """The linked account (refreshed from the container when unknown)."""
    cfg = await get_config(db)
    try:
        accts = (await api_get("/v1/accounts", timeout=6)).json() or []
    except Exception:  # noqa: BLE001
        return cfg.number
    num = accts[0] if accts else None
    if num != cfg.number:
        cfg.number = num
        await db.commit()
    return num


async def groups(number: str) -> list[dict]:
    r = await api_get(f"/v1/groups/{number}", timeout=30)
    out = []
    for g in r.json() or []:
        out.append({"id": g.get("internal_id") or g.get("id"), "send_id": g.get("id"),
                    "name": g.get("name") or "(unnamed group)", "members": len(g.get("members") or [])})
    return sorted(out, key=lambda g: g["name"].lower())


async def send_group(number: str, internal_id: str, text: str) -> None:
    gid = internal_id if internal_id.startswith("group.") else None
    if gid is None:
        import base64
        gid = "group." + base64.b64encode(internal_id.encode()).decode()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_url("/v2/send"), json={"message": text, "number": number, "recipients": [gid]})
        r.raise_for_status()


def _dedupe(group_id: str, ts_ms: int, sender: str, body: str) -> str:
    return hashlib.sha1(f"{group_id}|{ts_ms}|{sender}|{body[:200]}".encode()).hexdigest()


async def _store(db: AsyncSession, rows: list[dict]) -> int:
    if not rows:
        return 0
    for r in rows:
        r["dedupe"] = _dedupe(r["group_id"], r["ts_ms"], r["sender"], r["body"])
    stmt = insert(SignalMessage).values(rows).on_conflict_do_nothing(constraint="uq_signal_messages_dedupe")
    res = await db.execute(stmt)
    return res.rowcount or 0


# ── live receive ──────────────────────────────────────────────────────────────
def _envelope_rows(env: dict, watched: dict) -> list[dict]:
    e = env.get("envelope") or {}
    dm = e.get("dataMessage")
    sender = e.get("sourceName") or e.get("sourceNumber") or e.get("source") or "?"
    if dm is None:
        sent = ((e.get("syncMessage") or {}).get("sentMessage"))  # sent from the user's phone
        if sent is None:
            return []
        dm, sender = sent, "me"
    gi = dm.get("groupInfo") or {}
    gid = gi.get("groupId")
    if not gid or gid not in watched:
        return []
    body = dm.get("message") or ""
    att = len(dm.get("attachments") or [])
    if not body and not att:
        return []
    ts = int(dm.get("timestamp") or e.get("timestamp") or 0)
    return [{
        "group_id": gid, "group_name": watched[gid].get("name", ""), "sender": sender,
        "sent_at": datetime.fromtimestamp(ts / 1000, timezone.utc), "ts_ms": ts,
        "body": body, "attachments": att, "source": "live",
    }]


async def receive_once(db: AsyncSession) -> int:
    cfg = await get_config(db)
    num = cfg.number or await linked_number(db)
    if not num:
        return 0
    async with httpx.AsyncClient(timeout=90) as c:
        resp = await c.get(_url(f"/v1/receive/{num}"),
                           params={"timeout": 10, "ignore_attachments": "true", "send_read_receipts": "false"})
        if resp.status_code >= 400:
            cfg.last_error = f"receive {resp.status_code}: {resp.text[:200]}"
            await db.commit()
            return 0
        envs = resp.json() or []
    rows: list[dict] = []
    commands: list[dict] = []
    for env in envs if isinstance(envs, list) else []:
        rows += _envelope_rows(env, cfg.watched or {})
        if cfg.alert_group_id:
            commands += _envelope_rows(env, _AnyGroup(cfg.alert_group_id))
    n = await _store(db, rows)
    cfg.last_receive_at = datetime.now(timezone.utc)
    cfg.last_error = None
    await db.commit()
    if n:
        logger.info("signal: stored %d new message(s)", n)
    if commands:
        from app.services import netbot
        for r in commands:
            if netbot.is_alert_group(r["group_id"], cfg.alert_group_id):
                try:
                    await netbot.handle_message(db, r["body"], r["sender"], r["sent_at"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("netbot command failed: %s", exc)
    return n


class _AnyGroup(dict):
    """Lets _envelope_rows pass every group through (filtered by is_alert_group)."""
    def __init__(self, alert_gid: str):
        super().__init__()
        self._a = alert_gid

    def __contains__(self, gid) -> bool:  # noqa: D105
        return True

    def __getitem__(self, gid):  # noqa: D105
        return {"name": "alert"}


# ── history import (Signal Desktop export / pasted text) ─────────────────────
# signal-export (sigexport) markdown:  [2024-03-12 14:22] Dawid: text
_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]{1,60}):\s?(.*)$")
# generic "3/12/24, 2:22 PM - Dawid: text" (WhatsApp-style / copy-paste)
_LINE2 = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?\s*[APap][Mm]?)\s*[-–]\s*([^:]{1,60}):\s?(.*)$")


def _parse_dt(d: str, t: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%y %I:%M %p",
                "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M", "%m/%d/%y %I:%M:%S %p"):
        try:
            return datetime.strptime(f"{d} {t.strip().upper()}", fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_chat_text(text: str) -> list[tuple[datetime, str, str]]:
    out: list[list] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _LINE.match(line) or _LINE2.match(line)
        if m:
            dt = _parse_dt(m.group(1), m.group(2))
            if dt:
                out.append([dt, m.group(3).strip(), m.group(4)])
                continue
        if out and line.strip():
            out[-1][2] += "\n" + line  # continuation of a multi-line message
    return [(a, b, c.strip()) for a, b, c in out if c.strip()]


async def import_history(db: AsyncSession, filename: str, data: bytes, group_name: str) -> dict:
    texts: list[tuple[str, str]] = []  # (chat name, text)
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.filename.lower().endswith((".md", ".txt")) and info.file_size < 50_000_000:
                    parts = info.filename.strip("/").split("/")
                    chat = parts[-2] if len(parts) >= 2 else group_name
                    texts.append((group_name or chat, z.read(info).decode("utf-8", "replace")))
    else:
        texts.append((group_name or filename.rsplit(".", 1)[0], data.decode("utf-8", "replace")))
    total = added = 0
    chats = set()
    for chat, text in texts:
        msgs = parse_chat_text(text)
        if not msgs:
            continue
        chats.add(chat)
        gid = f"import:{chat}"
        rows = [{"group_id": gid, "group_name": chat, "sender": s, "sent_at": dt,
                 "ts_ms": int(dt.timestamp() * 1000), "body": b, "attachments": 0, "source": "import"}
                for dt, s, b in msgs]
        total += len(rows)
        for i in range(0, len(rows), 500):
            added += await _store(db, rows[i:i + 500])
    await db.commit()
    return {"chats": sorted(chats), "parsed": total, "added": added}


# ── equipment list → device names ─────────────────────────────────────────────
_MAC = re.compile(r"\b([0-9a-f]{2}(?:[:\-][0-9a-f]{2}){5})\b", re.I)
_TAG = re.compile(r"(?<![0-9a-z])#?([0-9a-f]{4})(?![0-9a-z])", re.I)


async def name_suggestions(db: AsyncSession) -> list[dict]:
    """Lines in 'hardware' chats that mention a device's MAC (or its last 4
    hex digits, like the #0906 tags) → the rest of the line as its name."""
    cfg = await get_config(db)
    hw = [gid for gid, v in (cfg.watched or {}).items() if v.get("role") == "hardware"]
    hw += [f"import:{v.get('name')}" for v in (cfg.watched or {}).values() if v.get("role") == "hardware"]
    imported = (await db.execute(select(SignalMessage.group_id).where(SignalMessage.source == "import").distinct())).scalars()
    hw += [g for g in imported if re.search(r"hard|equip|gear|inventory", g, re.I)]
    msgs = list((await db.execute(
        select(SignalMessage).where(SignalMessage.group_id.in_(hw)).order_by(SignalMessage.sent_at)
    )).scalars()) if hw else []
    devs = [d for d in (await db.execute(select(Device))).scalars() if d.mac]
    by_mac = {d.mac.lower().replace("-", ":"): d for d in devs}
    from app.services.names import split_name
    # Asset tags in the UniFi name — "(0903)", "#0906" — are what the crew
    # writes in the chat; MAC last-4 is the fallback.
    by_tag: dict[str, list[Device]] = {}
    for d in devs:
        tag = split_name(d.name)[1]
        if tag:
            by_tag.setdefault(tag.lower(), []).append(d)
    for d in devs:
        k = d.mac.lower().replace(":", "").replace("-", "")[-4:]
        if k not in by_tag:
            by_tag.setdefault(k, []).append(d)
    best: dict[str, dict] = {}
    for m in msgs:
        for line in m.body.splitlines():
            dev = None
            mm = _MAC.search(line)
            if mm:
                dev = by_mac.get(mm.group(1).lower().replace("-", ":"))
                rest = line.replace(mm.group(0), " ")
            else:
                for t in _TAG.finditer(line):
                    c = by_tag.get(t.group(1).lower())
                    if c and len(c) == 1:
                        dev, rest = c[0], line.replace(t.group(0), " ")
                        break
            if dev is None:
                continue
            name = re.sub(r"\s{2,}", " ", re.sub(r"^[\s\-–•*:|,]+|[\s\-–•*:|,]+$", "", rest)).strip()
            if not name or name.lower() == (dev.name or "").lower():
                continue
            best[dev.mac] = {"mac": dev.mac, "current": dev.name, "proposed": name[:60],
                             "type": dev.device_type, "line": line.strip()[:200],
                             "from": m.group_name, "at": m.sent_at.isoformat()}  # newest wins
    return sorted(best.values(), key=lambda x: (x["type"] != "switch", x["current"] or ""))
