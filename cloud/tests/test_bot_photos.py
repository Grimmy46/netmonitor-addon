"""Bot photo store without a database (the Postgres round trip is in
tests/pg_photos_check.py).

Run: cd cloud && python -m pytest tests/test_bot_photos.py -q
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import bot_photos


def row(**kw):
    base = dict(sent_at=datetime(2026, 11, 19, 0, 51, tzinfo=timezone.utc), sender="Dawid", mode="return",
                caption="AP - Rave Wave", tags_read=[], qr=[], labels=["U6.Fd.Circ.27"], matched_tag="0842",
                matched_device="U6.Fd.Circ.27", matched_via="label",
                result="📦 NETBOT: got it, Dawid.\n🧪 Practice — would log: #0842 returned.")
    return SimpleNamespace(**(base | kw))


def test_summary_text():
    s = bot_photos.summary_text(row())
    assert s.startswith("teardown photo from Dawid at 2026-11-18 5:51 PM (Arizona time)")
    assert 'caption: "AP - Rave Wave"' in s
    assert "read off the photo: label U6.Fd.Circ.27" in s
    assert "matched #0842 (UniFi U6.Fd.Circ.27) via label" in s
    assert "result: got it, Dawid. 🧪 Practice — would log: #0842 returned." in s
    assert "NETBOT" not in s and ".." not in s


def test_summary_text_nothing_read():
    s = bot_photos.summary_text(row(mode="deploy", caption="", labels=[], matched_tag=None, matched_device=None,
                                    result=None))
    assert s.startswith("setup photo") and "no caption" in s and "read off the photo: nothing" in s
    assert "no device matched" in s


def test_unlink_stays_inside_photo_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_photos, "_root", lambda: tmp_path)
    (tmp_path / "2026").mkdir()
    f = tmp_path / "2026" / "a.jpg"
    f.write_bytes(b"x")
    assert bot_photos._unlink("2026/a.jpg") and not f.exists()
    assert bot_photos._unlink("2026/missing.jpg")          # already gone is fine
    outside = tmp_path.parent / "keep.txt"
    outside.write_text("x")
    assert not bot_photos._unlink("../keep.txt") and outside.exists()
