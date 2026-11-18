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


@pytest.mark.parametrize("text,tag", [
    ("0951", "0951"),
    (" 0842 ", "0842"),
    ("https://inventory.example.com/assets?tag=0912", "0912"),
    ("https://inventory.example.com/asset/0910", "0910"),
    ("RCS-0951", "0951"),
    ("https://example.com/a/1234/b/5678", None),   # two numbers: don't guess
    ("hello", None),
])
def test_qr_tag(text, tag):
    assert photo_reader.qr_tag(text) == tag


def test_label_matching():
    names = ["U6.Fd.Circ.27", "U6.Fd.Circ.28", "AP - Rave Wave", "USW-Pro-Max-24 0912"]
    assert match_label("U6.Fd.Circ.27", names) == ["U6.Fd.Circ.27"]
    assert match_label("V6.Fd.Circ.27", names) == ["U6.Fd.Circ.27"]   # U read as V
    assert match_label("U6.Fd.Cire.27", names) == ["U6.Fd.Circ.27"]   # c read as e: 1 edit
    assert match_label("U6.Fd.Circ.2", names) == ["U6.Fd.Circ.27", "U6.Fd.Circ.28"]  # ambiguous
    assert match_label("Zz.Qq.Xx.99", names) == []
    assert match_label("ab", names) == []
    assert label_key("U6.Fd.Circ.27") == label_key("V6 Fd Circ,27")
