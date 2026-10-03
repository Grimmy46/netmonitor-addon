"""Short, human labels for UniFi device names (port of web/src/lib/names.ts).

"[Vivs Geni](0903) USW Pro Max 24 PoE" -> ("Vivs Geni", "0903")
"Kiosk 4 (0916) USW Pro Max 24 PoE"    -> ("Kiosk 4", "0916")
"""
import re

_MODEL_TAIL = re.compile(
    r"\s*\b(USW[\s-][\w\s.-]*|US-[\w-]+|UXG[\w-]*|UDM[\w-]*|UAP[\w-]*|"
    r"U6[\s-](Lite|LR|Pro|Mesh|Plus|Enterprise|IW|Extender)[\w\s-]*|U7[\s-][\w\s-]*|"
    r"AC Mesh[\w\s-]*|U6 Mesh)\s*$", re.I)


def _tidy(x: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\[\]()|]", " ", x)).strip()


def split_name(full: str | None) -> tuple[str, str | None]:
    raw = (full or "").strip()
    if not raw:
        return "Unnamed", None
    s, tag = raw, None
    m = re.search(r"[(\[](\d{3,4})[)\]]", s)
    if m:
        tag = m.group(1)
        s = s.replace(m.group(0), " ", 1)
    brackets = [b for b in (_tidy(x) for x in re.findall(r"\[([^\]]+)\]", s)) if b]
    outside = _tidy(_MODEL_TAIL.sub("", re.sub(r"\[[^\]]*\]", " ", s)))
    label = " · ".join([x for x in [outside, *brackets] if x]) or _tidy(s)
    if not label:
        return (f"#{tag}" if tag else raw), None
    return label, tag


def short(full: str | None) -> str:
    return split_name(full)[0]
