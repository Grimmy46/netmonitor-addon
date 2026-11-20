"""Event mode facade: pure logic (no database).

Run: cd cloud && python -m pytest tests/test_eventmode.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Account
from app.services import closure, eventmode, teardownbot

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)


def acc(**kw) -> Account:
    a = Account(name="t", event_mode="open", teardown_mode=False)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


_G = {"reopen_grace_until": None, "reopen_report": False}


def test_effects_table():
    assert eventmode.effects("setup") == {**_G, "alerts": False, "ap_restart": False, "bot": "deploy",
                                          "group_posts": "setup progress"}
    assert eventmode.effects("open") == {**_G, "alerts": True, "ap_restart": True, "bot": None,
                                         "group_posts": "outages"}
    assert eventmode.effects("teardown") == {**_G, "alerts": False, "ap_restart": False, "bot": "return",
                                             "group_posts": "teardown progress"}
    assert eventmode.effects("offseason") == {**_G, "alerts": False, "ap_restart": False, "bot": None,
                                              "group_posts": "none"}


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        eventmode.apply(acc(), "party", NOW)


def test_default_safety_end():
    a = acc()
    eventmode.apply(a, "teardown", NOW, by="d@x")
    assert a.teardown_mode is True and a.teardown_auto_off_at == NOW + timedelta(hours=18)
    assert a.event_mode_until == NOW + timedelta(hours=18)
    b = acc()
    eventmode.apply(b, "setup", NOW)
    assert b.teardown_auto_off_at == NOW + timedelta(hours=72)
    c = acc()
    eventmode.apply(c, "teardown", NOW, until=None)        # old toggle, hours=null
    assert c.teardown_auto_off_at is None


def test_teardown_until():
    a = acc()
    eventmode.apply(a, "teardown", NOW, by="d@x")
    assert a.event_mode == "teardown" and a.event_mode_since == NOW and a.event_mode_by == "d@x"
    until = NOW + timedelta(hours=6)
    eventmode.apply(a, "teardown", NOW + timedelta(minutes=5), until=until)
    assert a.teardown_auto_off_at == until and a.event_mode_until == until
    assert a.event_mode_since == NOW            # same mode: since unchanged
    assert a.teardown_since == NOW


def test_setup_uses_teardown_pause_path():
    a = acc()
    eventmode.apply(a, "setup", NOW, event_name="ASF 2026", venue="Phoenix", set_name=True,
                    set_venue=True)
    assert a.teardown_mode is True and a.event_mode == "setup"
    assert a.event_name == "ASF 2026" and a.event_venue == "Phoenix"
    assert a.closure_start is None


def test_offseason_is_a_long_closure_and_season_paused():
    a = acc()
    eventmode.apply(a, "offseason", NOW)
    assert a.teardown_mode is False
    assert a.closure_start == NOW and a.closure_end == NOW + eventmode.OFFSEASON_DEFAULT
    assert a.closure_note == eventmode.OFFSEASON_NOTE
    closure.set_window((a.closure_start, a.closure_end))
    try:
        assert closure.season_paused(NOW + timedelta(hours=1))
        assert closure.alerts_paused(NOW + timedelta(hours=1))
    finally:
        closure.set_window(None)


def test_offseason_until_sets_closure_end():
    a = acc()
    until = NOW + timedelta(days=90)
    eventmode.apply(a, "offseason", NOW, until=until)
    assert a.closure_end == until and a.event_mode_until == until


def test_open_from_offseason_reopens_now():
    a = acc()
    eventmode.apply(a, "offseason", NOW)
    later = NOW + timedelta(days=30)
    eventmode.apply(a, "open", later)
    assert a.closure_start == NOW and a.closure_end == later   # reopen grace + report still run
    assert a.teardown_mode is False and a.event_mode == "open"


def test_setup_from_offseason_keeps_closure_and_falls_back():
    a = acc()
    eventmode.apply(a, "offseason", NOW)
    end = a.closure_end
    t = NOW + timedelta(days=30)
    eventmode.apply(a, "setup", t)
    assert (a.closure_start, a.closure_end) == (NOW, end)       # not deleted
    assert a.teardown_mode is True and a.event_mode == "setup"
    # safety expiry (what the sweep does) -> back to off-season, not open
    a.teardown_mode = False
    assert eventmode.reconcile(a, t + timedelta(hours=73))
    assert a.event_mode == "offseason"


def test_old_toggle_in_offseason_pauses_on_top():
    a = acc()
    eventmode.apply(a, "offseason", NOW)
    end = a.closure_end
    a.teardown_mode = True                     # old toggle / DB edit, no apply()
    assert eventmode.reconcile(a, NOW + timedelta(hours=1)) and a.event_mode == "teardown"
    assert a.closure_end == end
    a.teardown_mode = False
    assert eventmode.reconcile(a, NOW + timedelta(hours=2)) and a.event_mode == "offseason"


def test_expiry_without_closure_goes_open():
    a = acc()
    eventmode.apply(a, "teardown", NOW)
    a.closure_start, a.closure_end = NOW, NOW + timedelta(hours=8)   # nightly close
    a.teardown_mode = False
    assert eventmode.reconcile(a, NOW + timedelta(hours=1)) and a.event_mode == "open"


def test_needs_confirm():
    a = acc()
    assert not eventmode.needs_confirm(a, "setup", NOW)
    eventmode.apply(a, "offseason", NOW)
    for m in ("setup", "teardown", "open"):
        assert eventmode.needs_confirm(a, m, NOW)
    assert not eventmode.needs_confirm(a, "offseason", NOW)
    eventmode.apply(a, "setup", NOW)                  # pause on top of the season
    assert eventmode.needs_confirm(a, "open", NOW)    # Open would end the closure
    assert not eventmode.needs_confirm(a, "teardown", NOW)
    assert "confirm" in eventmode.confirm_message("open")


def test_reopen_grace_exposed(monkeypatch):
    a = acc()
    eventmode.apply(a, "offseason", NOW)
    t = NOW + timedelta(days=10)
    eventmode.apply(a, "open", t)
    o = eventmode.out(a, t + timedelta(minutes=5))
    g = t + timedelta(hours=3)                       # closure_reopen_grace_hours default
    assert o["reopen_grace_until"] == g.isoformat()
    assert o["effects"]["reopen_grace_until"] == g.isoformat()
    assert o["effects"]["alerts"] is False and o["effects"]["reopen_report"] is True
    assert eventmode.out(a, g + timedelta(minutes=1))["reopen_grace_until"] is None
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "closure_reopen_report", False)
    assert eventmode.out(a, t + timedelta(minutes=5))["effects"]["reopen_report"] is False


def test_reopen_report_default_on():
    from app.core.config import get_settings
    assert get_settings().closure_reopen_report is True


def test_setup_replies_gated(monkeypatch):
    from app.core.config import get_settings
    assert get_settings().bot_setup_replies is False
    assert not teardownbot.reply_allowed("deploy")
    assert teardownbot.reply_allowed("return")
    monkeypatch.setattr(get_settings(), "bot_setup_replies", True)
    assert teardownbot.reply_allowed("deploy")


def test_open_clears_teardown_but_keeps_nightly_closure():
    a = acc()
    eventmode.apply(a, "teardown", NOW)
    # a short nightly "We're closed" closure, not ours
    a.closure_start, a.closure_end = NOW, NOW + timedelta(hours=10)
    eventmode.apply(a, "open", NOW + timedelta(hours=1))
    assert a.teardown_mode is False and a.teardown_since is None
    assert a.closure_end == NOW + timedelta(hours=10)


def test_name_and_venue_kept_unless_sent():
    a = acc(event_name="ASF", event_venue="Phoenix")
    eventmode.apply(a, "teardown", NOW)
    assert (a.event_name, a.event_venue) == ("ASF", "Phoenix")
    eventmode.apply(a, "open", NOW, venue="  ", set_venue=True)
    assert a.event_venue is None and a.event_name == "ASF"


def test_reconcile_legacy_toggles():
    a = acc()
    a.teardown_mode = True                          # old /agents/teardown or DB edit
    a.teardown_auto_off_at = NOW + timedelta(hours=18)
    assert eventmode.reconcile(a, NOW)
    assert a.event_mode == "teardown" and a.event_mode_until == a.teardown_auto_off_at
    a.teardown_mode = False                         # safety expiry in the sweep
    assert eventmode.reconcile(a, NOW)
    assert a.event_mode == "open" and a.event_mode_by == "auto"
    assert not eventmode.reconcile(a, NOW)          # stable


def test_reconcile_setup_expiry_goes_open():
    a = acc()
    eventmode.apply(a, "setup", NOW, until=NOW + timedelta(hours=1))
    a.teardown_mode = False
    assert eventmode.reconcile(a, NOW + timedelta(hours=2))
    assert a.event_mode == "open"


def test_reconcile_closures():
    a = acc(closure_start=NOW, closure_end=NOW + timedelta(days=5))
    assert eventmode.reconcile(a, NOW + timedelta(hours=1)) and a.event_mode == "offseason"
    assert not eventmode.reconcile(a, NOW + timedelta(days=2))
    assert eventmode.reconcile(a, NOW + timedelta(days=6)) and a.event_mode == "open"
    # a nightly closure (< 24 h) never flips Open
    b = acc(closure_start=NOW, closure_end=NOW + timedelta(hours=12))
    assert not eventmode.reconcile(b, NOW + timedelta(hours=1))


def test_out_shape():
    a = acc()
    eventmode.apply(a, "setup", NOW, by="d@x", event_name="ASF", set_name=True)
    o = eventmode.out(a)
    assert set(o) == {"mode", "since", "event_name", "venue", "until", "changed_by",
                      "reopen_grace_until", "effects"}
    assert o["mode"] == "setup" and o["since"] == NOW.isoformat() and o["changed_by"] == "d@x"
    assert eventmode.out(None)["mode"] == "open"


def test_announce_text():
    a = acc(event_name="ASF 2026", event_venue="Phoenix")
    eventmode.apply(a, "setup", NOW)
    t = eventmode.announce_text(a, "offseason")
    assert t.startswith("NETBOT: mode is now Setup (ASF 2026 @ Phoenix).") and "Off-season" in t


async def test_announce_disabled_by_default():
    from app.core.config import get_settings
    assert get_settings().mode_announce_rcsit is False
    assert await eventmode.announce(None, acc(), "open") is False   # never touches Signal


async def test_bot_deploy_practice_wording(monkeypatch):
    async def lookup(token, q):
        return {"http": 200, "results": [{"asset_tag": q, "matched_on": "asset_tag",
                                          "manufacturer": "Ubiquiti", "model": "U6"}]}

    async def no_return(*a, **k):
        raise AssertionError("must not mark returned in setup")
    monkeypatch.setattr(teardownbot, "lookup", lookup)
    monkeypatch.setattr(teardownbot, "mark_returned", no_return)
    monkeypatch.setattr(teardownbot, "_token", lambda td: "t")
    td = {"dry_run": False}
    r = await teardownbot.handle(td, "AP - Jumbo Cage #0951", "Mike", NOW, attachments=1,
                                 mode="deploy", venue="Phoenix")
    assert "Practice — would mark #0951 deployed at Phoenix" in r
    assert "practice mode" in r


async def test_bot_return_mode_unchanged(monkeypatch):
    async def lookup(token, q):
        return {"http": 200, "results": [{"asset_tag": q, "matched_on": "asset_tag"}]}

    async def ret(token, tag, who, at, dry, note, photo=None):
        return {"ok": True, "message": f"#{tag} returned by {who}."}
    monkeypatch.setattr(teardownbot, "lookup", lookup)
    monkeypatch.setattr(teardownbot, "mark_returned", ret)
    monkeypatch.setattr(teardownbot, "_token", lambda td: "t")
    r = await teardownbot.handle({"dry_run": False}, "#0951", "Mike", NOW, attachments=1)
    assert "✅ #0951 returned by Mike." in r


async def test_mark_deployed_is_a_stub():
    r = await teardownbot.mark_deployed("t", "0951", "Mike", NOW, "Phoenix")
    assert r["stub"] is True and r["ok"] is True
