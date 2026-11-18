"""Teardown bot + photo reader: which signal wins, and what the bot says.

Run: cd cloud && python -m pytest tests/test_teardown_photo.py -q
No network, no database: Inventory and the devices table are faked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import photo_reader, teardown_match, teardownbot

NOW = datetime(2026, 11, 18, 17, 51, tzinfo=timezone.utc)
TD = {"enabled": True, "group_id": "g", "token_enc": "x", "dry_run": True, "owner": "Dawid"}
PHOTO = {"bytes": b"jpeg", "mime": "image/jpeg", "name": "p.jpg"}

INVENTORY = {
    "0951": {"asset_tag": "0951", "manufacturer": "UniFi", "model": "U7 Pro", "primary_serial": "aabbccddee01"},
    "0912": {"asset_tag": "0912", "manufacturer": "UniFi", "model": "USW PRO MAX", "primary_serial": "aabbccddee02"},
    "0842": {"asset_tag": "0842", "manufacturer": "UniFi", "model": "U6 Mesh", "primary_serial": "aabbccddee27"},
}


async def fake_lookup(token, q):
    q = q.lower().replace(":", "")
    for tag, dev in INVENTORY.items():
        if q.lstrip("0") == tag.lstrip("0"):
            return {"http": 200, "results": [dev | {"matched_on": "asset_tag"}]}
        if q == dev["primary_serial"]:
            return {"http": 200, "results": [dev | {"matched_on": "primary_serial"}]}
    return {"http": 200, "results": []}


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDB:
    """Stands in for an AsyncSession: every select returns the devices rows."""
    rows = [
        ("U6.Fd.Circ.27", "aa:bb:cc:dd:ee:27", "ap", NOW - timedelta(minutes=20), False),
        ("U6.Fd.Circ.28", "aa:bb:cc:dd:ee:28", "ap", None, True),
        ("AP - Rave Wave 0999", "aa:bb:cc:dd:ee:99", "ap", None, True),
    ]

    async def execute(self, q):
        return FakeResult(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def bot(monkeypatch):
    calls = {"returned": [], "kept": [], "untagged": 0}
    monkeypatch.setattr(teardownbot, "_token", lambda td: "tok")
    monkeypatch.setattr(teardownbot, "lookup", fake_lookup)

    async def mark_returned(token, tag, who, at, dry, note, photo=None):
        calls["returned"].append(tag)
        return {"ok": True, "message": f"[DRY RUN] #{tag} returned by {who}."}
    monkeypatch.setattr(teardownbot, "mark_returned", mark_returned)

    async def keep(photo, body, who, sent_at, read, out):
        calls["kept"].append({"body": body, "read": read, "out": out})
    monkeypatch.setattr(teardownbot, "_keep_photo", keep)

    async def untagged(token, body, sent_at):
        calls["untagged"] += 1
        return {"ask": "I couldn't find a tag in that post."}
    monkeypatch.setattr(teardownbot, "_match_untagged", untagged)

    import app.core.db as db
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeDB())
    teardownbot._posted.clear()

    def reading(r):
        async def fake(data):
            return {"tags": [], "tag_conf": {}, "labels": [], "qr": [], "confidence": 0.0, "ms": 5} | r
        monkeypatch.setattr(photo_reader, "read_async", fake)
    calls["reading"] = reading
    return calls


async def test_caption_tag_wins_and_photo_agrees(bot):
    bot["reading"]({"tags": ["0951"], "tag_conf": {"0951": 1.0}, "qr": ["0951"], "confidence": 1.0})
    r = await teardownbot.handle(TD, "AP - Jumbo Cage #0951", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == ["0951"]
    assert "#0951 returned by Dawid" in r and "read" not in r
    assert bot["kept"][0]["out"]["via"] == "caption" and bot["kept"][0]["out"]["tag"] == "0951"


async def test_caption_and_photo_disagree_asks(bot):
    bot["reading"]({"tags": ["0912"], "tag_conf": {"0912": 0.96}, "confidence": 0.96})
    r = await teardownbot.handle(TD, "switch 0910", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == []
    assert "#0910" in r and "#0912" in r and "Which one" in r
    assert bot["kept"][0]["out"]["via"] == "conflict"


async def test_unsure_photo_read_does_not_contradict_caption(bot):
    bot["reading"]({"tags": ["0310"], "tag_conf": {"0310": 0.62}, "confidence": 0.62})
    r = await teardownbot.handle(TD, "switch 0910", "me", NOW, 1, photo=PHOTO)
    assert "Which one" not in r


async def test_photo_tag_when_caption_has_none(bot):
    bot["reading"]({"tags": ["0912"], "tag_conf": {"0912": 0.96}, "confidence": 0.96})
    r = await teardownbot.handle(TD, "switch", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == ["0912"]
    assert "read #0912 off the tag in the photo" in r
    assert bot["kept"][0]["out"]["via"] == "photo"


async def test_qr_tag_without_caption(bot):
    bot["reading"]({"tags": ["0951"], "tag_conf": {"0951": 1.0}, "qr": ["https://inv.example/asset/0951"],
                    "confidence": 1.0})
    r = await teardownbot.handle(TD, "", "Kim", NOW, 1, photo=PHOTO)
    assert bot["returned"] == ["0951"] and "QR code" in r and "Kim" in r
    assert bot["kept"][0]["out"]["via"] == "qr"


async def test_low_confidence_photo_tag_asks(bot):
    bot["reading"]({"tags": ["0310"], "tag_conf": {"0310": 0.9}, "confidence": 0.9})
    r = await teardownbot.handle(TD, "switch", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == [] and "#0310" in r and "type the 4-digit tag" in r


async def test_unifi_label_matched_to_device(bot):
    # OCR read U as V; the devices table has U6.Fd.Circ.27, whose MAC is #0842's serial
    bot["reading"]({"labels": ["V6.Fd.Circ.27"], "confidence": 0.6})
    r = await teardownbot.handle(TD, "AP - Rave Wave", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == ["0842"]
    assert 'UniFi "U6.Fd.Circ.27"' in r and "by MAC" in r
    out = bot["kept"][0]["out"]
    assert out["via"] == "label" and out["device"] == "U6.Fd.Circ.27" and out["tag"] == "0842"
    assert bot["untagged"] == 0


async def test_label_not_in_inventory_asks(bot, monkeypatch):
    bot["reading"]({"labels": ["U6.Fd.Circ.28"], "confidence": 0.6})
    r = await teardownbot.handle(TD, "AP", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == [] and "isn't in Inventory" in r and "U6.Fd.Circ.28" in r


async def test_nothing_on_photo_falls_back_to_offline_match(bot):
    bot["reading"]({})
    r = await teardownbot.handle(TD, "AP - Rave Wave", "me", NOW, 1, photo=PHOTO)
    assert bot["untagged"] == 1 and "couldn't find a tag" in r


async def test_meme_stays_quiet_and_is_not_kept(bot):
    bot["reading"]({})
    r = await teardownbot.handle(TD, "Gordon ramsey me", "me", NOW, 1, photo=PHOTO)
    assert r is None and bot["kept"] == [] and bot["returned"] == []


async def test_no_photo_no_reply(bot):
    assert await teardownbot.handle(TD, "check kiosk 6047", "me", NOW, 0) is None


async def test_ocr_switched_off(bot, monkeypatch):
    bot["reading"]({"tags": ["0912"], "tag_conf": {"0912": 0.96}})
    r = await teardownbot.handle(TD | {"photo_ocr": False}, "switch", "me", NOW, 1, photo=PHOTO)
    assert bot["returned"] == [] and bot["untagged"] == 1 and r


async def test_resolve_label_ambiguous():
    async def lk(q):
        return await fake_lookup("t", q)
    INVENTORY["0028"] = {"asset_tag": "0028", "primary_serial": "aabbccddee28"}
    try:
        r = await teardown_match.resolve_label(FakeDB(), ["U6.Fd.Circ.2"], lk)
    finally:
        del INVENTORY["0028"]
    assert r["status"] == "ambiguous" and {c.tag for c in r["cands"]} == {"0842", "0028"}


FIX = __import__("pathlib").Path(__file__).parent / "fixtures" / "photos"
real_ocr = pytest.mark.skipif(not photo_reader.available(), reason="tesseract not installed")


@real_ocr
async def test_real_photo_meme_is_ignored(bot, monkeypatch):
    monkeypatch.setattr(photo_reader, "read_async", _real_read_async)
    photo = {"bytes": (FIX / "meme_none.jpg").read_bytes(), "mime": "image/jpeg", "name": "m.jpg"}
    assert await teardownbot.handle(TD, "Gordon ramsey me", "me", NOW, 1, photo=photo) is None


@real_ocr
async def test_real_photo_tag_without_caption(bot, monkeypatch):
    monkeypatch.setattr(photo_reader, "read_async", _real_read_async)
    photo = {"bytes": (FIX / "tag_0912.jpg").read_bytes(), "mime": "image/jpeg", "name": "s.jpg"}
    r = await teardownbot.handle(TD, "switch", "me", NOW, 1, photo=photo)
    assert bot["returned"] == ["0912"] and "#0912" in r


async def _real_read_async(data):
    import asyncio
    return await asyncio.to_thread(photo_reader.read, data)
