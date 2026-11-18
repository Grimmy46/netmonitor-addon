"""Accuracy + speed of photo_reader on a labelled test set.

    python tests/eval_photo_reader.py [DIR]   # DIR has *.jpg + expected.json

A photo counts as correct when the tags match exactly and every expected UniFi
label is found (OCR'd label matched the way the bot matches it: folded
alphabet, 1 edit). Extra tags/labels on a photo that should have none = wrong.
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services import photo_reader  # noqa: E402
from app.services.teardown_match import match_label  # noqa: E402


def main(d: str) -> int:
    root = pathlib.Path(d)
    exp = json.loads((root / "expected.json").read_text())
    ok = 0
    times = []
    rows = []
    for name, want in sorted(exp.items()):
        data = (root / name).read_bytes()
        t = time.monotonic()
        got = photo_reader.read(data)
        times.append(time.monotonic() - t)
        tags_ok = got["tags"] == want["tags"]
        lab_ok = all(any(match_label(g, [w]) for g in got["labels"]) for w in want["labels"])
        lab_ok = lab_ok and bool(want["labels"] or not got["labels"])
        good = bool(tags_ok and lab_ok)
        ok += good
        rows.append(f"{'OK  ' if good else 'FAIL'} {name:34} tags={got['tags']} qr={got['qr']} "
                    f"labels={got['labels']} conf={got['confidence']} {got['ms']}ms")
    print("\n".join(rows))
    times.sort()
    print(f"\naccuracy {ok}/{len(exp)} = {ok / len(exp):.0%}; time median {times[len(times) // 2]:.2f}s, "
          f"max {times[-1]:.2f}s")
    return 0 if ok == len(exp) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/home/user/workspace/wt/ocr-testset"))
