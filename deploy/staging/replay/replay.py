"""Replay the ASF 2026 RCS-IT teardown posts through the bot (staging only)."""
import asyncio, json, os, sys
from datetime import datetime

assert os.environ.get("NETMON_STAGING") == "1", "staging only"
from app.core.security import encrypt
from app.services import teardownbot as tb

async def main():
    assert "mock" in tb.BASE
    msgs = json.load(open("/replay/msgs.json")); truth = {t["ts_ms"]: t["exp"] for t in json.load(open("/replay/truth.json"))}
    if os.environ.get("DRILLS"):
        msgs = json.load(open("/replay/drills.json")); truth = {m["ts_ms"]: m["exp"] for m in msgs}
    td = {"enabled": True, "group_id": "x", "token_enc": encrypt("mock"), "dry_run": False, "owner": "Dawid"}
    tb._posted.clear()
    got = []
    orig = tb.mark_returned
    async def spy(token, tag, *a, **k):
        got.append(tag); return await orig(token, tag, *a, **k)
    tb.mark_returned = spy
    rows = []
    for m in msgs:
        sent = datetime.fromisoformat(m["sent_at"])
        got.clear()
        sender = {"me": "Dawid"}.get(m["sender"], m["sender"].split()[0].title())
        reply = await tb.handle(td, m["body"], sender, sent, m["attachments"])
        ret = list(got)
        rows.append({"at": m["sent_at"][5:16], "by": sender, "body": m["body"][:60], "exp": truth[m["ts_ms"]],
                     "returned": ret, "reply": reply})
    json.dump(rows, sys.stdout)

asyncio.run(main())
