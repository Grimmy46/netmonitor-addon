"""Setup checklist: the plan's item list (from SitePlanner) + crew progress.

SitePlanner generates the checklist from the plan (every planned device and
cable run is one item) and saves it inside the plan JSON as `setupChecklist`.
Progress (status, evidence, measured cable lengths) lives separately in
`site_plans.checklist_progress`, keyed by item id, so a plan save can never
wipe it. Readers get the merged view from `merged()`.

Statuses follow the RCS evidence labels:
  planned   intended work only (the default)
  reported  someone says it's done, not checked against evidence
  verified  backed by evidence: a scan, a speed test, a direct check
            (manual) or a photo

Cable lengths from the plan are estimates (`est_length_m`, "Planned /
provisional"). Measured lengths are recorded separately and never overwrite
the estimate.

Pure functions only (no DB), so the bot can reuse them and they test cheaply.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from typing import Any

SCHEMA = "rcs.setup_checklist"
VERSION = 2
STATUSES = ("planned", "reported", "verified")
EVIDENCE_TYPES = ("photo", "scan", "manual", "speed_test")
ESTIMATE_LABEL = "Planned / provisional"
_PROGRESS_KEYS = ("status", "evidence", "evidence_log", "measured_length_m", "measured_by",
                  "measured_at", "note", "updated_at", "updated_by")
MAX_EVIDENCE_LOG = 50
M_TO_FT = 3.28084


class ChecklistError(ValueError):
    """Bad patch (unknown status, missing evidence, bad length …)."""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _blank_progress(item: dict) -> None:
    item["status"] = "planned"
    item["evidence"] = None
    item["evidence_log"] = []
    if item.get("kind") == "cable_run":
        item["measured_length_m"] = None
        item["measured_by"] = None
        item["measured_at"] = None


def normalize(checklist: dict | None) -> dict | None:
    """Any stored checklist (v1 or v2) -> v2, with progress fields blanked.

    Progress written into the plan JSON by a client is ignored on purpose:
    the server-side progress store is the only source. The one exception is a
    v1 item that was ticked `done` in the planner: it reads as `reported`
    (someone said so, nothing verified it)."""
    if not isinstance(checklist, dict):
        return None
    src_version = checklist.get("version") or 1
    out = copy.deepcopy(checklist)
    out["schema"] = SCHEMA
    out["version"] = VERSION
    out["upgraded_from"] = src_version if src_version != VERSION else checklist.get("upgraded_from")
    if out["upgraded_from"] is None:
        out.pop("upgraded_from")
    items = []
    for raw in checklist.get("items") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        it = copy.deepcopy(raw)
        legacy_done = src_version < 2 and bool(raw.get("done"))
        for k in ("done", "done_at", "done_by", "length_m", "length_ft", "map_m"):
            it.pop(k, None)
        if it.get("kind") == "cable_run":
            if src_version < 2:
                it["est_length_m"] = raw.get("length_m")
                it["est_length_ft"] = raw.get("length_ft")
                it["est_map_m"] = raw.get("map_m")
            it.setdefault("est_length_m", None)
            it.setdefault("est_length_ft", None)
            it.setdefault("est_map_m", None)
            it["estimate_label"] = ESTIMATE_LABEL
        _blank_progress(it)
        if legacy_done:
            it["status"] = "reported"
            it["note"] = raw.get("note") or "Ticked in the planner (v1 checklist)."
        items.append(it)
    out["items"] = items
    totals = dict(checklist.get("totals") or {})
    if src_version < 2:
        if "runs_measured" in totals:
            totals["runs_estimated"] = totals.pop("runs_measured")
        if "cable_m" in totals:
            totals["est_cable_m"] = totals.pop("cable_m")
        if "cable_ft" in totals:
            totals["est_cable_ft"] = totals.pop("cable_ft")
        for a in (totals.get("areas") or {}).values():
            if isinstance(a, dict):
                if "cable_m" in a:
                    a["est_cable_m"] = a.pop("cable_m")
                if "cable_ft" in a:
                    a["est_cable_ft"] = a.pop("cable_ft")
    totals["estimate_label"] = ESTIMATE_LABEL
    out["totals"] = totals
    return out


def merged(plan_checklist: dict | None, progress: dict | None) -> dict | None:
    """The v2 checklist with crew progress laid over the plan's items."""
    cl = normalize(plan_checklist)
    if cl is None:
        return None
    progress = progress or {}
    counts = {s: 0 for s in STATUSES}
    measured_m = 0.0
    runs_measured = 0
    for it in cl["items"]:
        p = progress.get(it["id"])
        if isinstance(p, dict):
            for k in _PROGRESS_KEYS:
                if k in p:
                    if k.startswith("measured_") and it.get("kind") != "cable_run":
                        continue
                    it[k] = copy.deepcopy(p[k])
        counts[it.get("status") if it.get("status") in STATUSES else "planned"] += 1
        if it.get("kind") == "cable_run" and _num(it.get("measured_length_m")) is not None:
            runs_measured += 1
            measured_m += float(it["measured_length_m"])
    cl["progress"] = {
        **counts,
        "total": len(cl["items"]),
        "runs_measured": runs_measured,
        "measured_cable_m": round(measured_m, 1),
        "measured_cable_ft": round(measured_m * M_TO_FT, 1),
    }
    return cl


def apply_patch(progress: dict | None, item: dict, patch: dict, actor: str,
                now: str | None = None) -> dict:
    """Return a NEW progress dict with `patch` applied to `item`.

    patch keys (all optional): status, evidence {type, ref, by?, at?},
    measured_length_m (cable runs only; null clears), measured_by,
    measured_at, note. Raises ChecklistError on bad input."""
    now = now or _now_iso()
    progress = copy.deepcopy(progress or {})
    iid = item["id"]
    cur = dict(progress.get(iid) or {})
    is_run = item.get("kind") == "cable_run"
    changed = False

    ev = patch.get("evidence")
    if ev is not None:
        if not isinstance(ev, dict):
            raise ChecklistError("evidence must be an object.")
        etype = ev.get("type")
        if etype not in EVIDENCE_TYPES:
            raise ChecklistError(f"evidence.type must be one of {', '.join(EVIDENCE_TYPES)}.")
        ref = ev.get("ref")
        if ref is not None and not isinstance(ref, (str, int, float)):
            raise ChecklistError("evidence.ref must be text.")
        rec = {"type": etype, "ref": None if ref is None else str(ref)[:500],
               "by": str(ev.get("by") or actor)[:120], "at": str(ev.get("at") or now)[:40]}
        cur["evidence"] = rec
        log = list(cur.get("evidence_log") or [])
        log.append(rec)
        cur["evidence_log"] = log[-MAX_EVIDENCE_LOG:]
        changed = True

    if "measured_length_m" in patch:
        if not is_run:
            raise ChecklistError("Only cable runs have a measured length.")
        m = patch["measured_length_m"]
        if m is None:
            cur["measured_length_m"] = cur["measured_by"] = cur["measured_at"] = None
        else:
            f = _num(m)
            if f is None or f <= 0 or f > 10000:
                raise ChecklistError("measured_length_m must be a length in metres (0–10000).")
            cur["measured_length_m"] = round(f, 2)
            cur["measured_by"] = str(patch.get("measured_by") or actor)[:120]
            cur["measured_at"] = str(patch.get("measured_at") or now)[:40]
        changed = True
    elif "measured_by" in patch or "measured_at" in patch:
        raise ChecklistError("measured_by/measured_at go with measured_length_m.")

    if "note" in patch:
        n = patch["note"]
        cur["note"] = None if n is None else str(n)[:1000]
        changed = True

    if "status" in patch:
        st = patch["status"]
        if st not in STATUSES:
            raise ChecklistError(f"status must be one of {', '.join(STATUSES)}.")
        if st == "verified" and not cur.get("evidence"):
            raise ChecklistError("verified needs evidence (photo, scan, manual check or speed test).")
        cur["status"] = st
        changed = True
    elif ev is not None and cur.get("status", "planned") == "planned":
        cur["status"] = "reported"   # evidence came in: at least reported

    if not changed:
        raise ChecklistError("Nothing to update.")
    cur["updated_at"] = now
    cur["updated_by"] = str(actor)[:120]
    progress[iid] = cur
    return progress


def find_item(plan_checklist: dict | None, item_id: str) -> dict | None:
    cl = normalize(plan_checklist)
    if not cl:
        return None
    return next((it for it in cl["items"] if it.get("id") == item_id), None)
