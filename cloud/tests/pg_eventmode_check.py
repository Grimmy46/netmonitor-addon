"""Event mode against a real, throwaway Postgres (NOT prod, NOT staging).

Exercises migration 0051 (derivation + idempotency) and the API facade
(GET/PUT /network/mode, the old /agents/teardown toggle, the sweep sync).

    # an empty local DB, e.g.  docker run -p 55432:5432 postgres:16-alpine
    export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=.. POSTGRES_DB=..
    cd cloud && python tests/pg_eventmode_check.py

It refuses to run unless the DB is empty or already at a 005x revision with
the marker account it creates, so it can't be pointed at real data by mistake.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

MARK = "eventmode-test"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    rows = await q("SELECT count(*) AS n FROM information_schema.tables WHERE table_name='accounts'")
    if rows[0]["n"]:
        names = [r["name"] for r in await q("SELECT name FROM accounts")]
        if any(n != MARK for n in names):
            raise SystemExit(f"refusing: accounts table has real rows {names}")


async def migration_checks():
    alembic("upgrade", "0050_close_stale_outages")
    # 0001 builds tables from today's models, so a fresh DB already has the
    # columns: drop them to get prod's pre-0051 shape.
    alembic("stamp", "0050_close_stale_outages")
    for col in ("event_mode", "event_mode_since", "event_mode_until", "event_mode_by",
                "event_name", "event_venue"):
        await q(f"ALTER TABLE accounts DROP COLUMN IF EXISTS {col}")
    await q("DELETE FROM accounts")
    await q("INSERT INTO accounts (id, name, teardown_mode, teardown_since, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :n, true, now(), now(), now())", n=MARK)
    alembic("upgrade", "0051_event_mode")
    r = (await q("SELECT event_mode, event_mode_by FROM accounts"))[0]
    ok(r["event_mode"] == "teardown" and r["event_mode_by"] == "migration", "0051 derives teardown")

    # idempotent: re-run with the columns present does not overwrite a chosen mode
    await q("UPDATE accounts SET event_mode='setup'")
    alembic("stamp", "0050_close_stale_outages")
    alembic("upgrade", "0051_event_mode")
    ok((await q("SELECT event_mode FROM accounts"))[0]["event_mode"] == "setup",
       "0051 re-run keeps chosen mode")

    # offseason derivation: a closure with > 24 h left
    alembic("downgrade", "0050_close_stale_outages")
    await q("UPDATE accounts SET teardown_mode=true, closure_start=now() - interval '1 day', "
            "closure_end=now() + interval '30 days'")
    alembic("upgrade", "0051_event_mode")
    ok((await q("SELECT event_mode FROM accounts"))[0]["event_mode"] == "offseason",
       "0051 derives offseason over teardown")
    before = (await q("SELECT event_mode, event_mode_since, event_mode_until, closure_start, "
                      "closure_end FROM accounts"))[0]
    ok(before["event_mode_until"] == before["closure_end"], "0051 offseason until = closure end")
    # re-run with the season closure still running: nothing changes
    alembic("stamp", "0050_close_stale_outages")
    alembic("upgrade", "0051_event_mode")
    after = (await q("SELECT event_mode, event_mode_since, event_mode_until, closure_start, "
                     "closure_end FROM accounts"))[0]
    ok(dict(after) == dict(before), "0051 re-run keeps offseason + closure as is")

    # open: short (nightly) closure, teardown off
    alembic("downgrade", "0050_close_stale_outages")
    await q("UPDATE accounts SET teardown_mode=false, closure_start=now(), "
            "closure_end=now() + interval '10 hours'")
    alembic("upgrade", "head")
    ok((await q("SELECT event_mode FROM accounts"))[0]["event_mode"] == "open", "0051 derives open")
    await q("UPDATE accounts SET closure_start=NULL, closure_end=NULL")


async def api_checks():
    import httpx

    from app.core.auth import current_user, require_admin
    from app.core.db import SessionLocal
    from app.main import app
    from app.services import closure, eventmode, teardownbot

    class U:
        email, role = "test@example.com", "admin"
    app.dependency_overrides[current_user] = lambda: U()
    app.dependency_overrides[require_admin] = lambda: U()
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://t") as c:
        r = (await c.get("/network/mode")).json()
        ok(r["mode"] == "open" and r["effects"]["alerts"] is True, f"GET open {r}")

        r = (await c.put("/network/mode", json={"mode": "setup", "event_name": "ASF 2026",
                                                "venue": "Phoenix"})).json()
        ok(r["mode"] == "setup" and r["venue"] == "Phoenix" and r["changed_by"] == U.email
           and r["effects"]["bot"] == "deploy", "PUT setup")
        a = (await q("SELECT teardown_mode, teardown_auto_off_at FROM accounts"))[0]
        left = a["teardown_auto_off_at"] - datetime.now(timezone.utc)
        ok(a["teardown_mode"] is True and timedelta(hours=71) < left <= timedelta(hours=72),
           "setup -> teardown_mode on, 72 h safety end")
        async with SessionLocal() as db:
            ok(await teardownbot.bot_mode(db) == "deploy", "bot listens: deploy")

        r = (await c.put("/network/mode", json={"mode": "teardown"})).json()
        ok(r["mode"] == "teardown" and r["venue"] == "Phoenix" and r["effects"]["bot"] == "return",
           "PUT teardown keeps venue")
        left = datetime.fromisoformat(r["until"]) - datetime.now(timezone.utc)
        ok(timedelta(hours=17) < left <= timedelta(hours=18), "teardown 18 h safety end")
        async with SessionLocal() as db:
            ok(await teardownbot.bot_mode(db) == "return", "bot listens: return")

        r = await c.put("/network/mode", json={"mode": "teardown",
                                               "until": "2020-01-01T00:00:00Z"})
        ok(r.status_code == 400, "past until rejected")
        r = await c.put("/network/mode", json={"mode": "party"})
        ok(r.status_code == 422, "bad mode rejected")

        until = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
        r = (await c.put("/network/mode", json={"mode": "offseason", "until": until})).json()
        ok(r["mode"] == "offseason" and r["until"] is not None, "PUT offseason")
        a = (await q("SELECT teardown_mode, closure_start, closure_end FROM accounts"))[0]
        ok(a["teardown_mode"] is False and a["closure_end"] is not None, "offseason -> closure window")
        ok(closure.season_paused(), "season_paused() true in offseason")
        async with SessionLocal() as db:
            ok(await teardownbot.bot_mode(db) is None, "bot quiet in offseason")
        cl = (await c.get("/network/closure")).json()["closure"]
        ok(cl and cl["phase"] == "closed", "old /network/closure shows the window")
        season = dict((await q("SELECT closure_start, closure_end FROM accounts"))[0])

        for m in ("setup", "teardown", "open"):
            r = await c.put("/network/mode", json={"mode": m})
            ok(r.status_code == 409 and "confirm" in r.json()["detail"], f"leave offseason -> {m} needs confirm")

        # old toggle in offseason: pause on top, closure intact, then back to offseason
        r = (await c.post("/agents/teardown", json={"enabled": True, "hours": 2})).json()
        ok(r["active"] is True and (await c.get("/network/mode")).json()["mode"] == "teardown",
           "old toggle in offseason -> teardown")
        ok(dict((await q("SELECT closure_start, closure_end FROM accounts"))[0]) == season,
           "closure intact under the old toggle")
        await c.post("/agents/teardown", json={"enabled": False})
        ok((await c.get("/network/mode")).json()["mode"] == "offseason", "toggle off -> offseason")

        # PUT setup with confirm: closure kept; expiry falls back to offseason
        r = (await c.put("/network/mode", json={"mode": "setup", "confirm": True})).json()
        ok(r["mode"] == "setup", "PUT setup with confirm")
        ok(dict((await q("SELECT closure_start, closure_end FROM accounts"))[0]) == season,
           "closure intact under setup")
        await q("UPDATE accounts SET teardown_mode=false")    # safety expiry
        async with SessionLocal() as db:
            await eventmode.sync(db)
        ok((await c.get("/network/mode")).json()["mode"] == "offseason", "setup expiry -> offseason")

        r = (await c.put("/network/mode", json={"mode": "open", "confirm": True})).json()
        ok(r["mode"] == "open" and not closure.season_paused(), "PUT open (confirm) ends season pause")
        ok(closure.phase() == "reopening", "open from offseason -> normal reopen grace")
        ok(r["reopen_grace_until"] is not None and r["effects"]["reopen_grace_until"]
           == r["reopen_grace_until"] and r["effects"]["reopen_report"] is True,
           "reopen_grace_until exposed")

        # old teardown toggle maps onto the mode
        r = (await c.post("/agents/teardown", json={"enabled": True, "hours": 2})).json()
        ok(r["active"] is True, "old POST /agents/teardown on")
        m = (await c.get("/network/mode")).json()
        ok(m["mode"] == "teardown" and m["until"] is not None, "-> mode teardown with until")
        r = (await c.post("/agents/teardown", json={"enabled": False})).json()
        ok(r["active"] is False and (await c.get("/network/mode")).json()["mode"] == "open",
           "old toggle off -> open")

        # safety expiry in the sweep folds back to open
        await c.put("/network/mode", json={"mode": "setup"})
        await q("UPDATE accounts SET teardown_mode=false")    # what _maybe_expire_teardown does
        async with SessionLocal() as db:
            await eventmode.sync(db)
        ok((await c.get("/network/mode")).json()["mode"] == "open", "expired setup -> open")

        # a long closure set via the old endpoint -> offseason
        now = datetime.now(timezone.utc)
        await c.put("/network/closure", json={"start": (now - timedelta(minutes=1)).isoformat(),
                                              "end": (now + timedelta(days=3)).isoformat()})
        ok((await c.get("/network/mode")).json()["mode"] == "offseason",
           "old long closure -> offseason")
        await c.delete("/network/closure")
        ok((await c.get("/network/mode")).json()["mode"] == "open", "closure cleared -> open")
    app.dependency_overrides.clear()


async def main():
    await guard()
    await migration_checks()
    await api_checks()
    print("all event-mode checks passed")


if __name__ == "__main__":
    asyncio.run(main())
