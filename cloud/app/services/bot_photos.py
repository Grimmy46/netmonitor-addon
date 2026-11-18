"""Keep the teardown/setup bot's photos for a while, then keep only text.

Every photo the bot acts on is written to settings.photo_dir (a Docker volume)
with a bot_photos row: caption, what was read off it, the device it matched
and the bot's reply. Once a day, rows older than settings.photo_keep_days get
a plain-text summary and their image file is deleted.

Files are laid out as <photo_dir>/<YYYY>/<MM>/<row id>.<ext>; the row stores the
relative path.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings

logger = logging.getLogger("netmonitor.bot_photos")

_EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp",
        "image/heic": "heic", "image/gif": "gif"}
RETENTION_INTERVAL_S = 24 * 3600
BATCH = 500


def _root() -> Path:
    return Path(get_settings().photo_dir)


def _write(rel: str, data: bytes) -> None:
    p = _root() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, p)


async def save(db, photo: dict, *, caption: str, sender: str, sent_at: datetime, mode: str = "return",
               read: dict | None = None, outcome: dict | None = None):
    """Store one photo + its row. A failed disk write still keeps the row (path NULL)."""
    from app.models import BotPhoto
    read = read or {}
    outcome = outcome or {}
    data: bytes = photo.get("bytes") or b""
    mime = (photo.get("mime") or "image/jpeg").lower()
    rid = uuid.uuid4()
    rel = f"{sent_at:%Y}/{sent_at:%m}/{rid}.{_EXT.get(mime, 'jpg')}"
    try:
        await asyncio.to_thread(_write, rel, data)
    except OSError as exc:
        logger.warning("could not store bot photo under %s: %s", _root(), exc)
        rel = None
    row = BotPhoto(
        id=rid, sent_at=sent_at, sender=(sender or "")[:120], mode=mode or "return", caption=caption or "",
        path=rel, mime=mime[:40], size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
        tags_read=read.get("tags") or [], labels=read.get("labels") or [], qr=read.get("qr") or [],
        ocr_confidence=read.get("confidence"), ocr_ms=read.get("ms"),
        matched_tag=outcome.get("tag"), matched_device=(outcome.get("device") or None),
        matched_via=outcome.get("via"), result=outcome.get("reply"),
    )
    db.add(row)
    await db.commit()
    return row


def summary_text(row) -> str:
    """One readable paragraph that replaces the photo."""
    from zoneinfo import ZoneInfo
    when = row.sent_at.astimezone(ZoneInfo("America/Phoenix")).strftime("%Y-%m-%d %-I:%M %p")
    kind = {"return": "teardown", "deploy": "setup"}.get(row.mode, row.mode)
    bits = [f"{kind} photo from {row.sender or '?'} at {when} (Arizona time)",
            f'caption: "{" ".join((row.caption or "").split())[:200]}"' if row.caption else "no caption"]
    read = []
    if row.tags_read:
        read.append("tag " + ", ".join(f"#{t}" for t in row.tags_read))
    if row.qr:
        read.append("QR " + ", ".join(str(q)[:80] for q in row.qr))
    if row.labels:
        read.append("label " + ", ".join(row.labels))
    bits.append("read off the photo: " + ("; ".join(read) if read else "nothing"))
    if row.matched_tag or row.matched_device:
        m = f"#{row.matched_tag}" if row.matched_tag else ""
        if row.matched_device:
            m += f" (UniFi {row.matched_device})" if m else f"UniFi {row.matched_device}"
        bits.append(f"matched {m}" + (f" via {row.matched_via}" if row.matched_via else ""))
    else:
        bits.append("no device matched")
    if row.result:
        res = " ".join(row.result.replace("📦 NETBOT:", "").split())
        bits.append(f"result: {res[:400].rstrip('.')}")
    return ". ".join(bits) + "."


def _unlink(rel: str) -> bool:
    root = _root().resolve()
    p = (root / rel).resolve()
    if root not in p.parents:  # never delete outside the photo volume
        return False
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    return True


async def summarize_old(db, now: datetime | None = None, keep_days: int | None = None) -> int:
    """Summarize + delete photos older than keep_days. Returns rows handled."""
    from app.models import BotPhoto
    now = now or datetime.now(timezone.utc)
    keep = keep_days if keep_days is not None else get_settings().photo_keep_days
    cutoff = now - timedelta(days=keep)
    rows = (await db.execute(select(BotPhoto).where(BotPhoto.sent_at < cutoff, BotPhoto.summarized_at.is_(None))
                             .order_by(BotPhoto.sent_at).limit(BATCH))).scalars().all()
    n = 0
    for row in rows:
        if row.path and not await asyncio.to_thread(_unlink, row.path):
            logger.warning("bot photo %s has a path outside photo_dir; left alone", row.id)
            continue
        row.summary = summary_text(row)
        row.path = None
        row.summarized_at = now
        n += 1
    if n:
        await db.commit()
        logger.info("bot photos: summarized and deleted %d photo(s) older than %d days", n, keep)
    return n


async def run_photo_retention() -> None:
    """Daily: summarize + delete old bot photos."""
    from app.core.db import SessionLocal
    await asyncio.sleep(120)  # let startup settle
    while True:
        try:
            async with SessionLocal() as db:
                while await summarize_old(db) == BATCH:
                    pass
        except Exception:  # noqa: BLE001
            logger.exception("bot photo retention failed")
        await asyncio.sleep(RETENTION_INTERVAL_S)
