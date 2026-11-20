"""Setup checklist progress against a real, throwaway Postgres (NOT prod, NOT staging).

Checks migration 0054 (adds site_plans.checklist_progress; safe to re-run,
reversible) and the API: a plan save never wipes progress, PATCH works for an
admin session and for an agent token (own site only), refuses anonymous
callers and bad input, and v1 checklists read as v2.

    # an empty local UTF8 DB, e.g.  docker run -p 55432:5432 postgres:16-alpine
    export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=.. POSTGRES_DB=..
    cd cloud && python tests/pg_checklist_check.py

Refuses to run if the database has accounts other than the marker it creates.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sqlalchemy import text  # noqa: E402

MARK = "checklist-test"


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


async def cols() -> set[str]:
    return {r["column_name"] for r in await q(
        "SELECT column_name FROM information_schema.columns WHERE table_name='site_plans'")}


async def migration_checks():
    alembic("upgrade", "0053_bot_photos")
    # 0001 builds tables from today's models: drop the column to get prod's pre-0054 shape.
    await q("ALTER TABLE site_plans DROP COLUMN IF EXISTS checklist_progress")
    alembic("stamp", "0053_bot_photos")
    alembic("upgrade", "0054_checklist_progress")
    ok("checklist_progress" in await cols(), "0054 adds site_plans.checklist_progress")
    alembic("stamp", "0053_bot_photos")
    alembic("upgrade", "0054_checklist_progress")
    ok("checklist_progress" in await cols(), "0054 re-run over an existing column is a no-op")
    alembic("downgrade", "0053_bot_photos")
    ok("checklist_progress" not in await cols(), "downgrade drops it")
    alembic("upgrade", "head")
    ok("checklist_progress" in await cols(), "upgrade head")


def plan_body(checklist: dict, version: int = 5) -> dict:
    return {"name": "Site plan", "schema_version": version,
            "data": {"version": version, "blocks": [], "runs": [{"id": 7}], "setupChecklist": checklist}}


CL_V2 = {"schema": "rcs.setup_checklist", "version": 2, "totals": {"runs_estimated": 1}, "items": [
    {"id": "dev:1", "kind": "device", "device_type": "ap", "name": "AP Jumbo", "area": "Q1"},
    {"id": "run:7", "kind": "cable_run", "name": "SB 1 → AP Jumbo", "area": "Q1",
     "est_length_m": 40, "est_length_ft": 132}]}


async def api_checks():
    import httpx

    from app.core.auth import COOKIE_NAME, make_session_token
    from app.core.security import hash_token
    from app.main import app

    acc, site, other, uid, vid, aid, aid2 = (uuid.uuid4() for _ in range(7))
    from app.core.db import SessionLocal
    from app.models.account import Account
    from app.models.agent import Agent
    from app.models.site import Site
    from app.models.user import User

    async with SessionLocal() as db:
        db.add(Account(id=acc, name=MARK))
        await db.flush()
        db.add_all([Site(id=site, account_id=acc, name="Main"), Site(id=other, account_id=acc, name="Other"),
                    User(id=uid, account_id=acc, email="admin@test", hashed_password="x", role="admin"),
                    User(id=vid, account_id=acc, email="viewer@test", hashed_password="x", role="viewer")])
        await db.flush()
        db.add_all([Agent(id=i, account_id=acc, site_id=s, name="TB1", token_hash=hash_token(t), status="online")
                    for i, s, t in ((aid, site, "tok-main"), (aid2, other, "tok-other"))])
        await db.commit()

    tr = httpx.ASGITransport(app=app)
    admin = {COOKIE_NAME: make_session_token(uid)}
    async with httpx.AsyncClient(transport=tr, base_url="http://t", cookies=admin) as c, \
            httpx.AsyncClient(transport=tr, base_url="http://t") as anon:
        base = f"/map/plans/{site}"
        r = await c.put(base, json=plan_body(CL_V2))
        ok(r.status_code == 204, f"save v5 plan {r.status_code}")

        r = await anon.patch(f"{base}/checklist/dev:1", json={"status": "reported"})
        ok(r.status_code == 401, f"no session, no token -> 401 ({r.status_code})")
        r = await anon.patch(f"{base}/checklist/dev:1", json={"status": "reported"},
                             cookies={COOKIE_NAME: make_session_token(vid)})
        ok(r.status_code == 403, f"viewer session -> 403 ({r.status_code})")
        r = await anon.patch(f"{base}/checklist/dev:1", json={"status": "reported"},
                             headers={"X-Agent-Token": "tok-other"})
        ok(r.status_code == 403, f"agent from another site -> 403 ({r.status_code})")
        r = await anon.patch(f"{base}/checklist/dev:1", json={"status": "reported"},
                             headers={"X-Agent-Token": "nope"})
        ok(r.status_code == 401, f"bad agent token -> 401 ({r.status_code})")

        r = await anon.patch(f"{base}/checklist/dev:1", headers={"X-Agent-Token": "tok-main"},
                             json={"evidence": {"type": "photo", "ref": "bot_photos/1"}})
        j = r.json()
        ok(r.status_code == 200 and j["status"] == "reported" and j["evidence"]["by"] == "agent:TB1",
           f"agent token (no browser session) adds evidence -> reported {r.status_code} {j}")
        r = await c.patch(f"{base}/checklist/dev:1",
                          json={"status": "verified", "evidence": {"type": "scan", "ref": "#0951"}})
        ok(r.status_code == 200 and r.json()["status"] == "verified"
           and r.json()["evidence"]["by"] == "admin@test", "admin verifies with a scan")
        r = await c.patch(f"{base}/checklist/run:7", json={"measured_length_m": 43.5})
        j = r.json()
        ok(j["measured_length_m"] == 43.5 and j["est_length_m"] == 40 and j["measured_by"] == "admin@test",
           "measured length stored apart from the estimate")

        r = await c.patch(f"{base}/checklist/run:7", json={"status": "verified"})
        ok(r.status_code == 422, "verified without evidence -> 422")
        r = await c.patch(f"{base}/checklist/dev:1", json={"measured_length_m": 3})
        ok(r.status_code == 422, "measured length on a device -> 422")
        r = await c.patch(f"{base}/checklist/run:99", json={"status": "reported"})
        ok(r.status_code == 404, "unknown item -> 404")
        r = await c.patch(f"{base}/checklist/dev:1", json={"status": "reported", "done": True})
        ok(r.status_code == 422, "unknown field -> 422")

        # a plan save (browser copy with stale/forged progress) must not wipe or override progress
        stale = {**CL_V2, "items": [dict(i, status="planned", measured_length_m=None) for i in CL_V2["items"]]}
        ok((await c.put(base, json=plan_body(stale))).status_code == 204, "re-save plan")
        cl = (await c.get(f"{base}/checklist")).json()
        dev, run = cl["items"]
        ok(dev["status"] == "verified" and run["measured_length_m"] == 43.5,
           "progress survives a plan save")
        ok(cl["progress"]["verified"] == 1 and cl["progress"]["runs_measured"] == 1 and cl["version"] == 2,
           f"merged progress counts {cl['progress']}")

        # old v1 checklist inside an old v4 plan still reads, as v2
        v1 = {"schema": "rcs.setup_checklist", "version": 1, "totals": {"runs_measured": 1, "cable_m": 25},
              "items": [{"id": "run:1", "kind": "cable_run", "length_m": 25, "length_ft": 80, "done": True}]}
        await c.put(f"/map/plans/{other}", json=plan_body(v1, version=4))
        cl = (await c.get(f"/map/plans/{other}/checklist")).json()
        ok(cl["version"] == 2 and cl["items"][0]["est_length_m"] == 25 and cl["items"][0]["status"] == "reported"
           and cl["totals"]["runs_estimated"] == 1, "v1 checklist reads as v2")


async def main():
    await guard()
    await migration_checks()
    await api_checks()
    print("all checklist checks passed")


if __name__ == "__main__":
    asyncio.run(main())
