"""Bot photos against a real, throwaway Postgres (NOT prod, NOT staging).

Checks migration 0053 (creates bot_photos; safe to re-run) and the photo
store: save → file on disk + row; after 90 days → text summary, file deleted.

    # an empty local DB, e.g.  docker run -p 55432:5432 postgres:16-alpine
    export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=.. POSTGRES_DB=..
    cd cloud && python tests/pg_photos_check.py

Refuses to run if the database has any devices or accounts (real data).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("PHOTO_DIR", tempfile.mkdtemp(prefix="nm-photos-"))
os.environ["PHOTO_STORE"] = "true"  # a temp dir is not a mount; allow files for the test

from sqlalchemy import text  # noqa: E402


def alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], cwd=HERE, check=True, capture_output=True)


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


async def q(sql: str, **kw):
    from app.core.db import engine
    async with engine.begin() as c:
        r = await c.execute(text(sql), kw)
        return r.mappings().all() if r.returns_rows else None


async def guard():
    for t in ("devices", "accounts"):
        n = await q(f"SELECT count(*) AS n FROM information_schema.tables WHERE table_name='{t}'")
        if n[0]["n"] and (await q(f"SELECT count(*) AS n FROM {t}"))[0]["n"]:
            raise SystemExit(f"refusing: {t} has rows — this looks like a real database")


async def has_table() -> bool:
    return bool((await q("SELECT count(*) AS n FROM information_schema.tables WHERE table_name='bot_photos'"))[0]["n"])


async def main():
    await guard()
    alembic("upgrade", "head")
    ok(await has_table(), "upgrade head: bot_photos exists")
    # prod shape before 0053: no table
    await q("DROP TABLE bot_photos")
    alembic("stamp", "0051_event_mode")
    alembic("upgrade", "0053_bot_photos")
    ok(await has_table(), "0053 creates bot_photos")
    # idempotent: re-run with the table (and index) present
    alembic("stamp", "0051_event_mode")
    alembic("upgrade", "0053_bot_photos")
    idx = await q("SELECT indexname FROM pg_indexes WHERE tablename='bot_photos'")
    ok("ix_bot_photos_sent_at" in {r["indexname"] for r in idx}, "0053 re-run is a no-op")
    alembic("upgrade", "head")

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.services import bot_photos
    root = Path(get_settings().photo_dir)
    now = datetime.now(timezone.utc)
    await q("DELETE FROM bot_photos")
    async with SessionLocal() as db:
        old = await bot_photos.save(
            db, {"bytes": b"\xff\xd8old", "mime": "image/jpeg"}, caption="AP - Jumbo Cage #0951", sender="Dawid",
            sent_at=now - timedelta(days=91),
            read={"tags": ["0951"], "qr": ["0951"], "labels": [], "confidence": 1.0, "ms": 420},
            outcome={"tag": "0951", "via": "caption", "reply": "📦 NETBOT: got it, Dawid.\n✅ #0951 returned."})
        new = await bot_photos.save(
            db, {"bytes": b"\xff\xd8new", "mime": "image/png"}, caption="AP - Rave Wave", sender="Kim",
            sent_at=now - timedelta(days=3), read={"labels": ["U6.Fd.Circ.27"]},
            outcome={"tag": "0842", "device": "U6.Fd.Circ.27", "via": "label", "reply": "ok"})
        old_path, new_path = root / old.path, root / new.path
    ok(old_path.read_bytes() == b"\xff\xd8old" and new_path.suffix == ".png", "save writes the files")
    async with SessionLocal() as db:
        n = await bot_photos.summarize_old(db, now=now)
    ok(n == 1, "only the 91-day-old photo is summarized")
    ok(not old_path.exists() and new_path.exists(), "old file deleted, new file kept")
    r = (await q("SELECT path, summary, summarized_at FROM bot_photos WHERE id=:i", i=old.id))[0]
    ok(r["path"] is None and r["summarized_at"] is not None, "row keeps no path")
    s = r["summary"]
    print("     summary:", s)
    ok("Jumbo Cage #0951" in s and "tag #0951" in s and "matched #0951 via caption" in s and "returned" in s,
       "summary has caption, tags read, match and result")
    async with SessionLocal() as db:
        ok(await bot_photos.summarize_old(db, now=now) == 0, "second run does nothing")
    # one file that can't be deleted must not hold up the batch
    await q("DELETE FROM bot_photos")
    async with SessionLocal() as db:
        rows = [await bot_photos.save(db, {"bytes": b"\xff\xd8" + bytes([i]), "mime": "image/jpeg"}, caption=f"p{i}",
                                      sender="Dawid", sent_at=now - timedelta(days=100 - i)) for i in range(4)]
    stuck = rows[1]
    real_unlink = bot_photos._unlink

    def flaky(rel):
        if rel == stuck.path:
            raise PermissionError(13, "Permission denied")
        return real_unlink(rel)
    bot_photos._unlink = flaky
    try:
        async with SessionLocal() as db:
            n = await bot_photos.summarize_old(db, now=now)
    finally:
        bot_photos._unlink = real_unlink
    ok(n == 4, "OSError on one row: all 4 rows handled")
    r = {x["caption"]: x for x in await q("SELECT caption, path, summary, summarized_at FROM bot_photos")}
    ok(all(r[f"p{i}"]["summarized_at"] for i in range(4)), "every row marked, so the next batch moves on")
    ok(r["p1"]["path"] == stuck.path and "image file not deleted: PermissionError" in r["p1"]["summary"],
       "failed row keeps its path and says why")
    ok(all(r[f"p{i}"]["path"] is None for i in (0, 2, 3)) and (root / stuck.path).exists(), "others deleted")
    async with SessionLocal() as db:
        ok(await bot_photos.summarize_old(db, now=now) == 0, "failed row is not picked up again as new")
        ok(await bot_photos.retry_failed_deletes(db) == 1, "retry removes the left-over file")
    r = (await q("SELECT path, summary FROM bot_photos WHERE caption='p1'"))[0]
    ok(r["path"] is None and "not deleted" not in r["summary"] and not (root / stuck.path).exists(),
       "after retry: no path, clean summary")
    # oldest first, ties by id
    await q("DELETE FROM bot_photos")
    same = now - timedelta(days=200)
    async with SessionLocal() as db:
        for i in range(3):
            await bot_photos.save(db, {"bytes": b"x"}, caption=f"t{i}", sender="s", sent_at=same)
    bot_photos.BATCH, old_batch = 2, bot_photos.BATCH
    try:
        async with SessionLocal() as db:
            await bot_photos.summarize_old(db, now=now)
    finally:
        bot_photos.BATCH = old_batch
    done = await q("SELECT id::text AS id FROM bot_photos WHERE summarized_at IS NOT NULL ORDER BY id")
    ids = [x["id"] for x in await q("SELECT id::text AS id FROM bot_photos ORDER BY id")]
    ok([d["id"] for d in done] == ids[:2], "same sent_at: the batch takes the lowest ids first")
    # photo storage off when the dir is not a mount and PHOTO_STORE is off
    await q("DELETE FROM bot_photos")
    os.environ["PHOTO_STORE"] = "false"
    get_settings.cache_clear()
    async with SessionLocal() as db:
        row = await bot_photos.save(db, {"bytes": b"x"}, caption="off", sender="s", sent_at=now)
    ok(row.path is None and not bot_photos.store_enabled(), "storage off: row kept, no file")
    os.environ["PHOTO_STORE"] = "true"
    get_settings.cache_clear()
    # a broken volume still records the row
    os.environ["PHOTO_DIR"] = "/proc/nope"
    get_settings.cache_clear()
    async with SessionLocal() as db:
        row = await bot_photos.save(db, {"bytes": b"x"}, caption="c", sender="s", sent_at=now)
    ok(row.path is None, "unwritable photo dir → row kept, path NULL")
    await q("DELETE FROM bot_photos")


if __name__ == "__main__":
    asyncio.run(main())
