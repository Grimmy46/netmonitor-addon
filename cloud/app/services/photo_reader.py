"""Read RCS asset tags, QR codes and UniFi labels from a teardown/setup photo.

Local and free: OpenCV for finding things, the `tesseract` binary for reading
them (apt package tesseract-ocr in the cloud image). No paid APIs.

    read(image_bytes) -> {"tags": ["0951"], "tag_conf": {"0951": 1.0},
                          "labels": ["U6.Fd.Circ.27"], "qr": ["0951"],
                          "confidence": 0.97, "ms": 640}

How it works:
  * QR: OpenCV's ArUco-based QR detector (it reads the small stickers much
    better than the classic one). The RCS sticker QR holds the tag itself or an
    Inventory URL; both are reported in `qr`, and a tag found in it goes into
    `tags` too.
  * RCS tag: the sticker is red ("PROPERTY OF Ray Cammack Shows") with a white
    strip holding a bold 4-digit number. We find red blobs, cut out the sticker
    around each one, and in each of the 4 orientations look for a row of 3-5
    digit-sized dark shapes. Only those shapes are redrawn, straightened and
    read by tesseract (digits only). The orientation where the red part sits
    above the number wins (that tells 0910 from an upside-down "0160").
  * UniFi label: dark text lines are found with a black-hat filter (any angle),
    straightened, and all read in one tesseract call (plus once upside down).
    Tokens that look like a UniFi name ("U6.Fd.Circ.27": letters and digits
    with 2+ dots/dashes) are reported. Matching them to a device is the
    caller's job (teardown_match.match_label copes with V6/U6-type misreads).
  * Sanity: a 4-digit number is only reported as a tag when it was read off a
    red sticker or its QR, so memes and screenshots yield nothing.

Everything is CPU-bound, so callers in async code should use `read_async`.
Runs in ~0.5-2 s per photo on 2 CPUs.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("netmonitor.photo_reader")

WORK_SIDE = 1600          # the photo is scaled so its long side is about this
STICKER_SIDE = 600        # each sticker cut-out is scaled to this
DIGIT_H = 40              # digit height fed to tesseract
LINE_H = 64               # text-line height fed to tesseract
MIN_TAG_CONF = 60.0       # tesseract word confidence (0-100) to accept a tag
TESS_TIMEOUT = 8

_TAG_RX = re.compile(r"^\d{4}$")
_QR_TAG = re.compile(r"(?:^|[^\d])(\d{4})(?:[^\d]|$)")
# UniFi-style name: letters+digits split by 2+ dots/dashes, e.g. U6.Fd.Circ.27
_LABEL_RX = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:[.\-_][A-Za-z0-9]+){2,}(?![A-Za-z0-9])")

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr")


def available() -> bool:
    return shutil.which("tesseract") is not None


def _tess(img, psm: int, whitelist: str | None = None) -> list[tuple[str, float]]:
    """(word, confidence) pairs from tesseract's TSV output."""
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return []
    cmd = ["tesseract", "stdin", "stdout", "--psm", str(psm)]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    cmd.append("tsv")
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        r = subprocess.run(cmd, input=buf.tobytes(), capture_output=True, timeout=TESS_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("tesseract failed: %s", exc)
        return []
    out = []
    for line in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
        p = line.split("\t")
        if len(p) == 12 and p[11].strip():
            try:
                out.append((p[11].strip(), float(p[10])))
            except ValueError:
                pass
    return out


# ── QR ────────────────────────────────────────────────────────────────────────
def _qr(img) -> list[str]:
    import cv2
    dets = []
    try:
        dets.append(cv2.QRCodeDetectorAruco())
    except AttributeError:  # older OpenCV
        pass
    dets.append(cv2.QRCodeDetector())
    found: list[str] = []
    for det in dets:
        for scale in (1.0, 2.0):
            x = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            try:
                ok, texts, pts, _ = det.detectAndDecodeMulti(x)
            except cv2.error:
                continue
            if not ok:
                continue
            found += [t for t in texts if t]
            # detected but not decoded: cut it out, clean it up and try again
            if pts is not None and not any(texts):
                for quad in pts:
                    found += _qr_retry(det, x, quad)
            if found:
                return list(dict.fromkeys(found))
    return list(dict.fromkeys(found))


def _qr_retry(det, img, quad) -> list[str]:
    import cv2
    import numpy as np
    q = np.array(quad, dtype=np.float32)
    side = 240
    dst = np.array([[0, 0], [side, 0], [side, side], [0, side]], dtype=np.float32)
    warp = cv2.warpPerspective(img, cv2.getPerspectiveTransform(q, dst), (side, side))
    g = cv2.cvtColor(warp, cv2.COLOR_BGR2GRAY) if warp.ndim == 3 else warp
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = cv2.copyMakeBorder(bw, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    for x in (bw, cv2.GaussianBlur(bw, (3, 3), 0)):
        try:
            t, _, _ = det.detectAndDecode(cv2.cvtColor(x, cv2.COLOR_GRAY2BGR))
        except cv2.error:
            continue
        if t:
            return [t]
    return []


def qr_tag(text: str) -> str | None:
    """'0951' or 'https://inventory…/asset/0951' → '0951'."""
    t = (text or "").strip()
    if _TAG_RX.match(t):
        return t
    m = re.search(r"(?:tag|asset[_-]?tag|asset)[=/:#]\s*0*?(\d{4})\b", t, re.I)
    if m:
        return m.group(1)
    nums = _QR_TAG.findall(t)
    return nums[-1] if len(nums) == 1 else None


# ── red sticker → 4-digit tag ─────────────────────────────────────────────────
def _red_mask(bgr):
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, 80, 45), (12, 255, 255)) | cv2.inRange(hsv, (165, 80, 45), (180, 255, 255))
    k = max(9, max(bgr.shape[:2]) // 80)  # bridge the QR, logo and text holes
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))


def _stickers(img) -> list:
    """Upright-ish crops around the biggest red blobs, with room for the white strip.

    Each blob's minimum-area rectangle gives the sticker's tilt; the crop is
    rotated by it so the sticker edges are level (the 90/180/270 question is
    settled later)."""
    import cv2
    m = _red_mask(img)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = img.shape[0] * img.shape[1]
    H, W = img.shape[:2]
    out, boxes = [], []
    for c in sorted(cs, key=cv2.contourArea, reverse=True)[:4]:
        a = cv2.contourArea(c)
        if a < area * 0.002:
            break
        hull = cv2.contourArea(cv2.convexHull(c)) or 1
        if a / hull < 0.6:  # a scattered red thing (cable, shirt), not a sticker
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(c)
        x, y, bw, bh = cv2.boundingRect(c)
        if any(_overlap((x, y, x + bw, y + bh), bx) > 0.6 for bx in boxes):
            continue
        boxes.append((x, y, x + bw, y + bh))
        if ang > 45:
            ang -= 90
        s = max(w, h)
        half_w, half_h = w / 2 + s * 0.55, h / 2 + s * 0.55
        rot = cv2.getRotationMatrix2D((cx, cy), ang, 1)
        # shift so the crop window lands at (0, 0)
        rot[0, 2] += half_w - cx
        rot[1, 2] += half_h - cy
        out.append(cv2.warpAffine(img, rot, (int(2 * half_w), int(2 * half_h)), borderMode=cv2.BORDER_REPLICATE))
    return out


def _overlap(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return ix * iy / small


def _digit_rows(g):
    """Rows of 3-5 similar dark digit-sized shapes, left to right."""
    import cv2
    th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    n, lab, st, _ = cv2.connectedComponentsWithStats(th)
    H = g.shape[0]
    comps = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if 0.05 * H < h < 0.4 * H and 0.25 < w / h < 1.0 and a > 0.2 * w * h:
            comps.append((int(x), int(y), int(w), int(h), i))
    comps.sort()
    used: set[int] = set()
    rows = []
    for i, c in enumerate(comps):
        if i in used:
            continue
        row, last = [c], c
        for j in range(i + 1, len(comps)):
            if j in used:
                continue
            d = comps[j]
            gap = d[0] - (last[0] + last[2])
            if (abs(d[3] - last[3]) < 0.25 * last[3]
                    and abs((d[1] + d[3] / 2) - (last[1] + last[3] / 2)) < 0.4 * last[3]
                    and -0.05 * last[3] < gap < 0.8 * last[3]):
                row.append(d)
                last = d
                used.add(j)
        if 3 <= len(row) <= 5:
            rows.append(row)
    return lab, rows


def _render_row(lab, row):
    """Only the row's shapes, black on white, straightened, digits DIGIT_H tall."""
    import cv2
    import numpy as np
    m = np.isin(lab, [r[4] for r in row]).astype(np.uint8) * 255
    c0, c1 = row[0], row[-1]
    ang = math.degrees(math.atan2((c1[1] + c1[3] / 2) - (c0[1] + c0[3] / 2),
                                  (c1[0] + c1[2] / 2) - (c0[0] + c0[2] / 2)))
    x0 = min(r[0] for r in row); y0 = min(r[1] for r in row)
    x1 = max(r[0] + r[2] for r in row); y1 = max(r[1] + r[3] for r in row)
    pad = int((y1 - y0) * 0.6)
    m = m[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    rot = cv2.getRotationMatrix2D((m.shape[1] / 2, m.shape[0] / 2), ang, 1)
    m = cv2.warpAffine(m, rot, (m.shape[1], m.shape[0]))
    ys, xs = np.nonzero(m > 127)
    if not len(ys):
        return None
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    k = DIGIT_H / max(1, m.shape[0])
    m = cv2.resize(m, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    return cv2.copyMakeBorder(255 - m, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)


def _sticker_tags(img) -> list[tuple[str, float]]:
    """[(tag, conf 0-1)] read off red stickers."""
    import cv2
    out: dict[str, float] = {}
    rots = (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_180)
    for crop in _stickers(img):
        k = STICKER_SIDE / max(crop.shape[:2])
        crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        red = _red_mask(crop)
        for rot in rots:
            g = gray if rot is None else cv2.rotate(gray, rot)
            rm = red if rot is None else cv2.rotate(red, rot)
            lab, rows = _digit_rows(g)
            for row in rows:
                # upright sticker: the red part is above the number
                cy = sum(r[1] + r[3] / 2 for r in row) / len(row)
                ys = rm.nonzero()[0]
                upright = len(ys) and ys.mean() < cy
                bw = _render_row(lab, row)
                if bw is None:
                    continue
                for word, conf in _tess(bw, 7, "0123456789"):
                    if _TAG_RX.match(word) and conf >= MIN_TAG_CONF:
                        c = conf / 100 * (1.0 if upright else 0.6)
                        out[word] = max(out.get(word, 0.0), c)
    # drop pieces of a longer read ("d.Circ.27" when "U6.Fd.Circ.27" was found)
    out = {w: c for w, c in out.items() if not any(w != o and w in o for o in out)}
    return sorted(out.items(), key=lambda kv: -kv[1])


# ── text lines → UniFi labels ─────────────────────────────────────────────────
def _rotate_any(gray, deg: float):
    """Rotate with the canvas grown to fit."""
    import cv2
    H, W = gray.shape
    m = cv2.getRotationMatrix2D((W / 2, H / 2), deg, 1)
    c, s_ = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(H * s_ + W * c), int(H * c + W * s_)
    m[0, 2] += nw / 2 - W / 2
    m[1, 2] += nh / 2 - H / 2
    return cv2.warpAffine(gray, m, (nw, nh), borderMode=cv2.BORDER_REPLICATE)


def _line_rects(gray):
    import cv2
    import numpy as np
    H, W = gray.shape
    k = max(3, int(min(H, W) / 120))
    bh = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k * 5, k * 5)))
    _, th = cv2.threshold(bh, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    rects = []
    for kern in ((k * 4, max(1, k // 2)), (max(1, k // 2), k * 4)):  # horizontal, vertical text
        cl = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, kern))
        cl = cv2.morphologyEx(cl, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        cs, _ = cv2.findContours(cl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cs:
            (cx, cy), (w, h), a = cv2.minAreaRect(c)
            long_, short = max(w, h), min(w, h)
            if short < 8 or long_ < 4 * short or long_ > 0.9 * max(H, W) or short > 0.1 * max(H, W):
                continue
            rects.append(((cx, cy), (w, h), a, long_ * short))
    return rects


def _text_lines(gray, limit: int = 8):
    """Straightened crops of dark-text lines at any angle, text LINE_H tall.

    The closing kernels find lines within ~±25° of level or upright, so the
    search also runs on a 45°-turned copy to cover steep tilts."""
    import cv2
    found = []
    for base in (gray, _rotate_any(gray, 45)):
        found += [(r, base) for r in _line_rects(base)]
    found.sort(key=lambda rb: -rb[0][3])
    out = []
    for ((cx, cy), (w, h), a, _), base in found[:limit + 8]:
        if w < h:
            w, h, a = h, w, a + 90
        H, W = base.shape
        rot = cv2.warpAffine(base, cv2.getRotationMatrix2D((cx, cy), a, 1), (W, H), borderMode=cv2.BORDER_REPLICATE)
        pw, ph = w * 1.1 + 10, h * 1.3 + 6
        x0, y0 = int(cx - pw / 2), int(cy - ph / 2)
        c = rot[max(0, y0):int(y0 + ph), max(0, x0):int(x0 + pw)]
        if c.size == 0:
            continue
        f = LINE_H / max(1, c.shape[0])
        c = cv2.normalize(cv2.resize(c, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC), None, 0, 255,
                          cv2.NORM_MINMAX)
        if _texty(c):
            out.append(c)
        if len(out) >= limit:
            break
    return out


def _texty(c) -> bool:
    """A real text line breaks into several letter-sized shapes; edges and shadows don't."""
    import cv2
    _, bw = cv2.threshold(c, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, st, _ = cv2.connectedComponentsWithStats(bw)
    H = c.shape[0]
    return sum(1 for i in range(1, n) if 0.25 * H < st[i][3] < 0.9 * H and st[i][2] < 1.2 * st[i][3]) >= 4


def _stack(crops):
    import cv2
    import numpy as np
    W = max(c.shape[1] for c in crops) + 40
    return np.vstack([cv2.copyMakeBorder(c, 14, 14, 20, W - 20 - c.shape[1], cv2.BORDER_CONSTANT, value=255)
                      for c in crops])


_SEP_RX = re.compile(r"[A-Za-z0-9][.\-_][A-Za-z0-9]")


def _labels(words: list[tuple[str, float]]) -> list[tuple[str, float]]:
    out: dict[str, float] = {}
    # tesseract sometimes splits one label in two ("[U6.F" "d.Circ.27f"): also try neighbours joined
    pairs = [(a + b, min(ca, cb)) for (a, ca), (b, cb) in zip(words, words[1:], strict=False)
             if _SEP_RX.search(a) and _SEP_RX.search(b)]
    for word, conf in [*words, *pairs]:
        # tape edges come out as brackets/bars stuck to the text: "(U6.Fd.Circ.27]f"
        for m in _LABEL_RX.finditer(word):
            w = m.group(0)
            if len(w) >= 6 and any(ch.isalpha() for ch in w) and any(ch.isdigit() for ch in w):
                # the strict format is the real filter; tesseract's confidence
                # is low on short labels with tape edges around them
                out[w] = max(out.get(w, 0.0), 0.5 + conf / 200)
    # drop pieces of a longer read ("d.Circ.27" when "U6.Fd.Circ.27" was found)
    out = {w: c for w, c in out.items() if not any(w != o and w in o for o in out)}
    return sorted(out.items(), key=lambda kv: -kv[1])


# ── entry points ──────────────────────────────────────────────────────────────
def _load(data: bytes):
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    k = WORK_SIDE / max(img.shape[:2])
    if k < 1 or k > 1.25:  # downscale big photos; upscale small ones so text is readable
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    return img


def _brighten(img):
    """Lift dark photos (night teardown, flash-less) before reading."""
    import cv2
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    if lab[..., 0].mean() > 90:
        return img
    lab[..., 0] = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def read(data: bytes) -> dict:
    """Read one photo. Never raises; returns empty lists when nothing is found."""
    t0 = time.monotonic()
    empty = {"tags": [], "tag_conf": {}, "labels": [], "qr": [], "confidence": 0.0, "ms": 0}
    try:
        import cv2
        img = _load(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("photo decode failed: %s", exc)
        img = None
    if img is None:
        return empty | {"error": "not an image"}
    if not available():
        logger.warning("tesseract is not installed; reading QR codes only")
    try:
        img = _brighten(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        futs = []
        lines = _text_lines(gray) if available() else []
        if lines:
            # all text lines in one tesseract call, plus the same upside down
            futs = [_pool.submit(_tess, _stack(lines), 4),
                    _pool.submit(_tess, _stack([cv2.rotate(c, cv2.ROTATE_180) for c in lines]), 4)]
        qr = _qr(img)
        stick = _sticker_tags(img) if available() else []
        words = [w for f in futs for w in f.result()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("photo read failed: %s", exc)
        return empty | {"ms": int((time.monotonic() - t0) * 1000), "error": type(exc).__name__}
    tags: dict[str, float] = {}
    for q in qr:
        t = qr_tag(q)
        if t:
            tags[t] = 1.0
    for t, c in stick:
        if t in tags:
            continue
        # a lone low-confidence read (e.g. upside down) loses to a confident one
        if tags and c < 0.8 * max(tags.values()):
            continue
        tags[t] = c
    # keep only reads close to the best one: one sticker per photo is the norm
    if tags:
        best = max(tags.values())
        tags = {t: c for t, c in tags.items() if c >= 0.75 * best}
    labels = _labels(words)
    conf = max([*tags.values(), *(c for _, c in labels), 0.0])
    return {
        "tags": sorted(tags, key=lambda t: -tags[t]),
        "tag_conf": {t: round(c, 2) for t, c in tags.items()},   # 1.0 = from the QR
        "labels": [w for w, _ in labels],
        "qr": qr,
        "confidence": round(conf, 2),
        "ms": int((time.monotonic() - t0) * 1000),
    }


async def read_async(data: bytes) -> dict:
    return await asyncio.to_thread(read, data)
