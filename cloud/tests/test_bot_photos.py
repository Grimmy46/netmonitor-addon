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


# ── storage switch ────────────────────────────────────────────────────────────
def _settings(monkeypatch, **kw):
    from app.core.config import get_settings
    for k, v in kw.items():
        monkeypatch.setattr(get_settings(), k, v)


def test_store_off_unless_mount_or_setting(tmp_path, monkeypatch):
    _settings(monkeypatch, photo_dir=str(tmp_path), photo_store=False)
    assert not bot_photos.store_enabled()                      # plain folder (e.g. the image's /data/photos)
    monkeypatch.setattr(bot_photos.os.path, "ismount", lambda p: p == str(tmp_path))
    assert bot_photos.store_enabled()                          # a mounted volume
    monkeypatch.setattr(bot_photos.os.path, "ismount", lambda p: False)
    _settings(monkeypatch, photo_store=True)
    assert bot_photos.store_enabled()                          # PHOTO_STORE=true


class _DB:
    def __init__(self, rows=None):
        self.rows, self.added, self.commits, self.sql = rows or [], [], 0, None

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def execute(self, q):
        self.sql = str(q)
        rows = self.rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


async def test_save_without_store_writes_no_file(tmp_path, monkeypatch):
    _settings(monkeypatch, photo_dir=str(tmp_path), photo_store=False)
    db = _DB()
    r = await bot_photos.save(db, {"bytes": b"\xff\xd8x", "mime": "image/jpeg"}, caption="c", sender="s",
                              sent_at=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert r.path is None and db.added == [r] and list(tmp_path.iterdir()) == []


async def test_save_with_store_writes_file(tmp_path, monkeypatch):
    _settings(monkeypatch, photo_dir=str(tmp_path), photo_store=True)
    r = await bot_photos.save(_DB(), {"bytes": b"\xff\xd8x", "mime": "image/jpeg"}, caption="c", sender="s",
                              sent_at=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert (tmp_path / r.path).read_bytes() == b"\xff\xd8x"


# ── retention ─────────────────────────────────────────────────────────────────
async def test_retention_survives_os_errors(tmp_path, monkeypatch):
    _settings(monkeypatch, photo_dir=str(tmp_path))
    rows = [row(path=f"2026/{i}.jpg", summary=None, summarized_at=None, id=i) for i in range(3)]

    def unlink(rel):
        if rel == "2026/1.jpg":
            raise OSError(30, "Read-only file system")
        return True
    monkeypatch.setattr(bot_photos, "_unlink", unlink)
    db = _DB(rows)
    now = datetime(2027, 3, 1, tzinfo=timezone.utc)
    assert await bot_photos.summarize_old(db, now=now, keep_days=90) == 3
    assert "ORDER BY bot_photos.sent_at, bot_photos.id" in db.sql
    assert [r.path for r in rows] == [None, "2026/1.jpg", None]
    assert all(r.summarized_at == now for r in rows) and db.commits == 1
    assert "image file not deleted: OSError: Read-only file system" in rows[1].summary


async def test_retention_survives_a_bad_row(tmp_path, monkeypatch):
    _settings(monkeypatch, photo_dir=str(tmp_path))
    bad = row(path=None, summary=None, summarized_at=None, id=1, sent_at=None)   # summary_text will fail
    good = row(path=None, summary=None, summarized_at=None, id=2)
    db = _DB([bad, good])
    assert await bot_photos.summarize_old(db, now=datetime(2027, 3, 1, tzinfo=timezone.utc), keep_days=90) == 2
    assert "could not be summarized" in bad.summary and good.summary.startswith("teardown photo")
