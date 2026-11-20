"""photo_reader on the real crew photos (cropped from Signal) + rotated,
tilted, blurred and dark variants made on the fly.

Run: cd cloud && python -m pytest tests/test_photo_reader.py -q
Needs the `tesseract` binary (apt tesseract-ocr); skipped without it.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from app.services import photo_reader
from app.services.teardown_match import label_key, match_label

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

FIX = pathlib.Path(__file__).parent / "fixtures" / "photos"
EXPECTED = json.loads((FIX / "expected.json").read_text())
needs_tess = pytest.mark.skipif(not photo_reader.available(), reason="tesseract not installed")


def _rot(img, deg):
    fixed = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    if deg in fixed:
        return cv2.rotate(img, fixed[deg])
    h, w = img.shape[:2]
    return cv2.warpAffine(img, cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1), (w, h),
                          borderMode=cv2.BORDER_REPLICATE)


def _dark(img, f=0.35):
    return np.clip(img.astype(np.float32) * f, 0, 255).astype(np.uint8)


VARIANTS = {
    "orig": lambda i: i,
    "rot90": lambda i: _rot(i, 90),
    "rot180": lambda i: _rot(i, 180),
    "rot270": lambda i: _rot(i, 270),
    "tilt15": lambda i: _rot(i, 15),
    "tilt-20": lambda i: _rot(i, -20),
    "blur": lambda i: cv2.GaussianBlur(i, (0, 0), 1.6),
    "dark": _dark,
    "darkblur": lambda i: cv2.GaussianBlur(_dark(i, 0.45), (0, 0), 1.2),
}


def _jpeg(name: str, variant: str) -> bytes:
    img = cv2.imread(str(FIX / name))
    ok, buf = cv2.imencode(".jpg", VARIANTS[variant](img), [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    return buf.tobytes()


@needs_tess
@pytest.mark.parametrize("variant", list(VARIANTS))
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_reads_photo(name, variant):
    want = EXPECTED[name]
    t = time.monotonic()
    got = photo_reader.read(_jpeg(name, variant))
    took = time.monotonic() - t
    assert got["tags"] == want["tags"], got
    for label in want["labels"]:
        assert any(match_label(g, [label]) for g in got["labels"]), got
    if not want["labels"]:
        assert got["labels"] == [], got
    if not want["tags"] and not want["labels"]:
        assert got["confidence"] == 0.0 and got["qr"] == []
    assert took < 3.0, f"{took:.2f}s"


@needs_tess
def test_big_photo_is_fast():
    """A phone-sized photo (4000 px) is scaled down first."""
    img = cv2.imread(str(FIX / "tag_0912.jpg"))
    big = cv2.resize(img, None, fx=4000 / img.shape[0], fy=4000 / img.shape[0], interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", big, [cv2.IMWRITE_JPEG_QUALITY, 90])
    t = time.monotonic()
    got = photo_reader.read(buf.tobytes())
    assert got["tags"] == ["0912"]
    assert time.monotonic() - t < 3.0


def test_not_an_image():
    got = photo_reader.read(b"definitely not a jpeg")
    assert got["tags"] == [] and got["labels"] == [] and got["qr"] == [] and got["confidence"] == 0.0


def test_blank_image():
    ok, buf = cv2.imencode(".png", np.full((400, 600, 3), 200, np.uint8))
    got = photo_reader.read(buf.tobytes())
    assert got["tags"] == [] and got["labels"] == []


@pytest.fixture
def inv_host(monkeypatch):
    monkeypatch.setattr(photo_reader, "_qr_hosts", lambda: {"inventory.example.com"})


@pytest.mark.parametrize("text,tag", [
    ("0951", "0951"),
    (" 0842 ", "0842"),
    ("#0912", "0912"),
    ("https://inventory.example.com/assets?tag=0912", "0912"),
    ("https://inventory.example.com/asset/0910", "0910"),
    ("https://inventory.example.com/devices/0910/", "0910"),
    ("https://INVENTORY.example.com/item?asset_tag=0951", "0951"),
    ("https://evil.example.net/asset/0910", None),          # not the Inventory host
    ("https://inventory.example.com/manual/page/0910.pdf", None),
    ("https://inventory.example.com/assets?tag=09101", None),
    ("ftp://inventory.example.com/asset/0910", None),
    ("RCS-0951", None),                                     # not the tag format
    ("WIFI:S:Show WiFi;T:WPA;P:secret1234;;", None),
    ("https://example.com/a/1234/b/5678", None),
    ("12345", None),
    ("hello", None),
])
def test_qr_tag(inv_host, text, tag):
    assert photo_reader.qr_tag(text) == tag


def test_qr_hosts_come_from_settings(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "inventory_qr_hosts", " inv.rcs.example , other.example ")
    assert photo_reader._qr_hosts() == {"inv.rcs.example", "other.example"}
    assert photo_reader.qr_tag("https://inv.rcs.example/asset/0910") == "0910"


def _qr_png(text: str) -> bytes:
    q = cv2.QRCodeEncoder.create().encode(text)
    q = cv2.resize(q, None, fx=10, fy=10, interpolation=cv2.INTER_NEAREST)
    q = cv2.copyMakeBorder(q, 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=255)
    return cv2.imencode(".png", cv2.cvtColor(q, cv2.COLOR_GRAY2BGR))[1].tobytes()


def test_foreign_qr_is_ignored(inv_host):
    for text in ("WIFI:S:Show WiFi;T:WPA;P:secret1234;;", "https://evil.example.net/asset/0910",
                 "https://www.ui.com/qr/U6-Mesh/0910"):
        got = photo_reader.read(_qr_png(text))
        assert got["qr"] == [] and got["tags"] == [], text


def test_rcs_qr_is_read(inv_host):
    got = photo_reader.read(_qr_png("https://inventory.example.com/asset/0910"))
    assert got["qr"] == ["https://inventory.example.com/asset/0910"] and got["tags"] == ["0910"]
    assert photo_reader.read(_qr_png("0951"))["tags"] == ["0951"]


# ── limits: size, memory, time, concurrency ───────────────────────────────────
def test_header_size():
    img = np.full((300, 500, 3), 128, np.uint8)
    for ext in (".jpg", ".png", ".webp"):
        assert photo_reader.image_size(cv2.imencode(ext, img)[1].tobytes()) == (500, 300), ext
    prog = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_PROGRESSIVE, 1])[1].tobytes()
    assert photo_reader.image_size(prog) == (500, 300)
    assert photo_reader.image_size(b"GIF89a....") is None


def test_over_25_megapixels_is_refused_before_decoding(monkeypatch):
    import struct
    import zlib
    # a valid PNG header claiming 6000 x 5000 (30 MP), with almost no data
    ihdr = struct.pack(">IIBBBBB", 6000, 5000, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
           + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)))
    called = []
    monkeypatch.setattr(cv2, "imdecode", lambda *a: called.append(a))
    got = photo_reader.read(png)
    assert got["error"].startswith("too big") and got["tags"] == [] and called == []


def test_unknown_format_is_refused():
    assert photo_reader.read(b"GIF89a" + b"\0" * 100)["error"] == "not a JPEG, PNG or WebP image"


def test_big_jpeg_is_decoded_reduced(monkeypatch):
    flags = []
    real = cv2.imdecode

    def spy(buf, flag):
        flags.append(flag)
        return real(buf, flag)
    big = cv2.imencode(".jpg", np.full((4000, 3000, 3), 128, np.uint8))[1].tobytes()
    monkeypatch.setattr(cv2, "imdecode", spy)
    img, why = photo_reader._load(big)
    assert flags == [cv2.IMREAD_REDUCED_COLOR_2] and why is None
    assert max(img.shape[:2]) == photo_reader.WORK_SIDE


def test_budget_stops_reading(monkeypatch):
    calls = []

    def slow_run(cmd, **kw):
        calls.append(kw["timeout"])
        time.sleep(0.3)
        raise photo_reader.subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(photo_reader, "available", lambda: True)
    monkeypatch.setattr(photo_reader.subprocess, "run", slow_run)
    data = (FIX / "tag_0912.jpg").read_bytes()
    t = time.monotonic()
    got = photo_reader.read(data, budget=1.0)
    took = time.monotonic() - t
    assert took < 2.0 and got.get("timed_out")
    assert all(x <= 1.0 for x in calls)          # each tesseract call gets what's left, not 8 s
    assert photo_reader.read(data, deadline=time.monotonic() - 1)["error"] == "timed out"


def test_row_cap_per_crop(monkeypatch):
    rows = [[(i * 30 + j * 10, 100, 8, 20, 1) for j in range(4)] for i in range(25)]
    monkeypatch.setattr(photo_reader, "_digit_rows", lambda g: (np.zeros(g.shape, np.int32), rows))
    monkeypatch.setattr(photo_reader, "_render_row", lambda lab, row: np.zeros((40, 80), np.uint8))
    monkeypatch.setattr(photo_reader, "_stickers", lambda img: [np.zeros((300, 300, 3), np.uint8)])
    n = []
    monkeypatch.setattr(photo_reader, "_tess", lambda *a, **k: n.append(1) or [])
    photo_reader._sticker_tags(np.zeros((300, 300, 3), np.uint8))
    assert len(n) == photo_reader.MAX_ROWS_PER_CROP   # 4 rotations x 25 rows found, 6 read


async def test_one_photo_at_a_time_and_busy_when_queue_full(monkeypatch):
    import asyncio
    import threading
    state = {"now": 0, "max": 0}
    lock = threading.Lock()
    gate = threading.Event()

    def fake_read(data, budget, deadline):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        gate.wait(5)
        with lock:
            state["now"] -= 1
        return {"tags": [], "labels": [], "qr": [], "budget": budget}
    monkeypatch.setattr(photo_reader, "read", fake_read)
    tasks = [asyncio.ensure_future(photo_reader.read_async(b"x", budget=7)) for _ in range(photo_reader.MAX_QUEUE)]
    await asyncio.sleep(0.2)
    busy = await photo_reader.read_async(b"x")
    assert busy["error"] == "busy"
    gate.set()
    res = await asyncio.gather(*tasks)
    assert state["max"] == 1 and all(r["budget"] == 7 for r in res)
    assert photo_reader._waiting == 0


def test_label_matching():
    names = ["U6.Fd.Circ.27", "U6.Fd.Circ.28", "AP - Rave Wave", "USW-Pro-Max-24 0912"]
    assert match_label("U6.Fd.Circ.27", names) == ["U6.Fd.Circ.27"]
    assert match_label("V6.Fd.Circ.27", names) == ["U6.Fd.Circ.27"]   # U read as V
    assert match_label("U6.Fd.Cire.27", names) == ["U6.Fd.Circ.27"]   # c read as e: 1 edit
    assert match_label("U6.Fd.Circ.2", names) == ["U6.Fd.Circ.27", "U6.Fd.Circ.28"]  # ambiguous
    assert match_label("Zz.Qq.Xx.99", names) == []
    assert match_label("ab", names) == []
    assert label_key("U6.Fd.Circ.27") == label_key("V6 Fd Circ,27")
