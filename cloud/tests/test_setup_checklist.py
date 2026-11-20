"""Setup checklist (v2): v1 upgrade, progress merge, patch rules.

Run: cd cloud && python -m pytest tests/test_setup_checklist.py -q
(The DB round trip, migration 0054 and the PATCH endpoint are in
tests/pg_checklist_check.py.)
"""
from __future__ import annotations

import pytest

from app.services import setup_checklist as sc
from app.services.setup_checklist import ChecklistError

V1 = {
    "schema": "rcs.setup_checklist", "version": 1,
    "totals": {"devices": 1, "runs": 1, "runs_measured": 1, "cable_m": 25, "cable_ft": 80, "spools_1000ft": 1,
               "areas": {"Q1": {"runs": 1, "cable_m": 25, "cable_ft": 80}}},
    "items": [
        {"id": "dev:1", "kind": "device", "device_type": "gateway", "name": "GW 1", "area": "Q1",
         "done": True, "status": "done"},
        {"id": "run:1", "kind": "cable_run", "name": "GW 1 → SB 1", "area": "Q1", "map_m": 15.7,
         "length_m": 25, "length_ft": 80, "done": False, "status": "todo"},
    ],
}


def v2(**over):
    base = {"schema": "rcs.setup_checklist", "version": 2, "totals": {"runs_estimated": 1}, "items": [
        {"id": "dev:1", "kind": "device", "device_type": "ap", "name": "AP 1", "status": "verified",
         "evidence": {"type": "manual"}},
        {"id": "run:7", "kind": "cable_run", "name": "SB 1 → AP 1", "est_length_m": 40, "est_length_ft": 132,
         "measured_length_m": 99},
    ]}
    return base | over


def test_v1_upgrades_to_v2():
    cl = sc.normalize(V1)
    assert cl["version"] == 2 and cl["upgraded_from"] == 1
    t = cl["totals"]
    assert t["runs_estimated"] == 1 and "runs_measured" not in t
    assert t["est_cable_m"] == 25 and t["est_cable_ft"] == 80 and "cable_m" not in t
    assert t["areas"]["Q1"] == {"runs": 1, "est_cable_m": 25, "est_cable_ft": 80}
    assert t["estimate_label"] == "Planned / provisional"
    dev, run = cl["items"]
    assert dev["status"] == "reported" and "done" not in dev     # ticked in v1 = reported, not verified
    assert run["status"] == "planned"
    assert run["est_length_m"] == 25 and run["est_length_ft"] == 80 and run["est_map_m"] == 15.7
    assert "length_m" not in run and run["measured_length_m"] is None
    assert run["estimate_label"] == "Planned / provisional"


def test_client_progress_in_plan_json_is_ignored():
    """A plan save carries whatever the browser had; only server progress counts."""
    cl = sc.normalize(v2())
    dev, run = cl["items"]
    assert dev["status"] == "planned" and dev["evidence"] is None
    assert run["measured_length_m"] is None and run["est_length_m"] == 40


def test_merge_overlays_progress_and_counts():
    prog = {"dev:1": {"status": "verified", "evidence": {"type": "scan", "ref": "#0951", "by": "bot", "at": "t"}},
            "run:7": {"status": "reported", "measured_length_m": 43.5, "measured_by": "Dawid", "measured_at": "t"},
            "run:gone": {"status": "verified"}}
    cl = sc.merged(v2(), prog)
    dev, run = cl["items"]
    assert dev["status"] == "verified" and dev["evidence"]["ref"] == "#0951"
    assert run["measured_length_m"] == 43.5 and run["est_length_m"] == 40    # kept separate
    assert cl["progress"] == {"planned": 0, "reported": 1, "verified": 1, "total": 2, "runs_measured": 1,
                              "measured_cable_m": 43.5, "measured_cable_ft": round(43.5 * 3.28084, 1)}


def test_merge_none():
    assert sc.merged(None, {"x": {}}) is None


ITEM_DEV = {"id": "dev:1", "kind": "device"}
ITEM_RUN = {"id": "run:7", "kind": "cable_run"}


def test_patch_evidence_then_verify():
    p = sc.apply_patch({}, ITEM_DEV, {"evidence": {"type": "photo", "ref": "bot_photos/abc"}}, "bot", now="T1")
    e = p["dev:1"]
    assert e["status"] == "reported"                                  # evidence lifts planned -> reported
    assert e["evidence"] == {"type": "photo", "ref": "bot_photos/abc", "by": "bot", "at": "T1"}
    assert e["updated_by"] == "bot" and len(e["evidence_log"]) == 1
    p2 = sc.apply_patch(p, ITEM_DEV, {"status": "verified", "evidence": {"type": "scan", "ref": "#0951",
                                                                          "by": "Ann", "at": "T0"}}, "x", now="T2")
    e2 = p2["dev:1"]
    assert e2["status"] == "verified" and e2["evidence"]["type"] == "scan" and e2["evidence"]["by"] == "Ann"
    assert [x["type"] for x in e2["evidence_log"]] == ["photo", "scan"]
    assert p["dev:1"]["status"] == "reported"                          # input not mutated


def test_verified_needs_evidence():
    with pytest.raises(ChecklistError, match="evidence"):
        sc.apply_patch({}, ITEM_DEV, {"status": "verified"}, "a")
    assert sc.apply_patch({}, ITEM_DEV, {"status": "reported"}, "a")["dev:1"]["status"] == "reported"


@pytest.mark.parametrize("patch,msg", [
    ({"status": "done"}, "status"),
    ({"evidence": {"type": "rumour"}}, "evidence.type"),
    ({"evidence": "photo"}, "object"),
    ({"measured_length_m": -3}, "metres"),
    ({"measured_length_m": "abc"}, "metres"),
    ({"measured_by": "x"}, "go with"),
    ({}, "Nothing"),
])
def test_patch_rejects(patch, msg):
    with pytest.raises(ChecklistError, match=msg):
        sc.apply_patch({}, ITEM_RUN, patch, "a")


def test_measured_length_runs_only_and_clear():
    with pytest.raises(ChecklistError, match="cable runs"):
        sc.apply_patch({}, ITEM_DEV, {"measured_length_m": 10}, "a")
    p = sc.apply_patch({}, ITEM_RUN, {"measured_length_m": 41.257, "measured_by": "Dawid"}, "admin", now="T")
    assert p["run:7"]["measured_length_m"] == 41.26 and p["run:7"]["measured_by"] == "Dawid"
    assert p["run:7"]["measured_at"] == "T" and p["run:7"].get("status") is None
    p = sc.apply_patch(p, ITEM_RUN, {"measured_length_m": None}, "admin")
    assert p["run:7"]["measured_length_m"] is None and p["run:7"]["measured_by"] is None


def test_speed_test_evidence():
    p = sc.apply_patch({}, ITEM_RUN, {"status": "verified", "evidence": {"type": "speed_test", "ref": "940 Mbps"}},
                       "agent:TB1")
    assert p["run:7"]["status"] == "verified" and p["run:7"]["evidence"]["ref"] == "940 Mbps"


def test_find_item():
    assert sc.find_item(V1, "run:1")["est_length_m"] == 25
    assert sc.find_item(V1, "run:9") is None and sc.find_item(None, "dev:1") is None
