"""Symptom-guided analysis (symptoms.py): the offline "what's it doing?" layer.

The taxonomy must recognize plain-English complaints, relate them to the
diagnostic findings the detectors ACTUALLY produced (never invent), and call
out when the log doesn't cover the situation the user described.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from tuneassist import symptoms
from tuneassist.core import analyze_log, SessionOpts
from tuneassist.engine_gm import Config

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _opts(**kw):
    return SessionOpts(cfg=Config(), **kw)


def test_match_recognizes_plain_english():
    cases = {
        "idles rough and hunts at stoplights": {"rough_idle"},
        "bogs when I floor it": {"hesitation"},
        "I can hear it pinging under load": {"knock"},
        "smells like gas at idle, black smoke": {"runs_rich"},
        "won't start when it's cold, long crank": {"hard_start", "cold_running"},
        "surges at highway speed": {"surge_cruise"},
        "falls flat under boost": {"no_power", "boost_issue"},
        "down on power up top": {"no_power"},
    }
    for text, expect in cases.items():
        got = {m["id"] for m in symptoms.match(text)}
        assert expect <= got, f"{text!r}: expected {expect}, got {got}"
    assert symptoms.match("the paint is peeling") == []
    assert symptoms.match("") == []
    assert symptoms.match(None) == []


def test_expanded_symptoms_recognized():
    # the v0.1.25 expansion: newer complaint classes users actually type
    cases = {
        "it lugs and chugs going up a hill": {"lugging"},
        "exhaust pops and crackles when I let off": {"decel_pop"},
        "stalls in gear when I come to a stop": {"stalls_load"},
        "dies when I turn the AC on": {"stalls_load"},
        "the RPM hangs at 1500 after I let off the throttle": {"idle_hang"},
        "won't idle back down": {"idle_hang"},
        "it cuts out at 6500 and hits a wall": {"power_cut"},
        "shudders at highway speed": {"shudder"},
        "shifts really hard into second": {"trans_shift"},
        "won't lock up the converter": {"trans_shift"},
        "check engine light is on, threw a P0171": {"cel"},
        "terrible gas mileage lately": {"bad_mpg"},
        "the fuel trims are all over the place": {"trims_drift"},
        "it floods when I try to start it": {"flooding"},
        "stalls until it warms up": {"cold_stall"},
    }
    for text, expect in cases.items():
        got = {m["id"] for m in symptoms.match(text)}
        assert expect <= got, f"{text!r}: expected {expect}, got {got}"


def test_taxonomy_ids_are_real_and_unique():
    # every finding_id a symptom points at must be a REAL detector id (a typo'd
    # id would silently never pin), and symptom ids must be unique.
    import re
    ids = set()
    for mod in ("diagnostics", "crank"):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tuneassist", mod + ".py"), encoding="utf-8").read()
        ids |= set(re.findall(r"Finding\(\s*[\"']([A-Z0-9_]+)", src))
    sym_ids = [s["id"] for s in symptoms.TAXONOMY]
    assert len(sym_ids) == len(set(sym_ids)), "duplicate symptom id"
    assert len(symptoms.TAXONOMY) >= 26, "expected the expanded taxonomy"
    for s in symptoms.TAXONOMY:
        for fid in s["finding_ids"]:
            assert fid in ids, f"{s['id']} points at unknown finding {fid}"
        assert s["region"] in (None, *symptoms.REGION_LABEL), s["id"]


def test_negation_is_not_a_match():
    # a symptom mentioned only to say it's NOT happening must not be recognized
    for text in ("it doesn't ping anymore", "no longer stalls when hot",
                 "not running hot", "the knock went away", "used to bog but not now"):
        assert symptoms.match(text) == [] or all(
            m["id"] not in ("knock", "dies_hot", "overheat", "hesitation")
            for m in symptoms.match(text)), text
    # ...but a real complaint that merely CONTAINS a negator word still matches
    assert any(m["id"] == "no_power" for m in symptoms.match("no power up top"))
    assert any(m["id"] == "hard_start" for m in symptoms.match("it will not start"))


def test_emphatic_typos_normalize():
    assert any(m["id"] == "hesitation" for m in symptoms.match("it bogggs when I floor it"))
    assert any(m["id"] == "rough_idle" for m in symptoms.match("idles rouuugh"))
    assert any(m["id"] == "knock" for m in symptoms.match("I hear knnnock"))


def test_region_coverage_flags_missing_regions():
    n = 2000
    # an idle-only log: no WOT, no cruise, warm the whole time
    df = pd.DataFrame({"rpm": np.full(n, 800.0), "map": np.full(n, 35.0),
                       "tps": np.zeros(n), "ect": np.full(n, 195.0)})
    col = {"rpm": "rpm", "map": "map", "tps": "tps", "ect": "ect"}
    cov = symptoms.region_coverage(df, col, Config())
    assert cov["idle"] is True
    assert cov["wot"] is False and cov["boost"] is False
    assert cov["cruise"] is False and cov["warmup"] is False


def test_relate_and_reorder_pin_matching_findings():
    from tuneassist.diagnostics import Finding
    findings = [Finding("LEAN_CRUISE", "warning", "t", "d"),
                Finding("IDLE_HUNT", "warning", "t", "d"),
                Finding("LOW_VOLTAGE", "info", "t", "d")]
    matched = symptoms.match("rough idle")
    related, gaps = symptoms.relate(matched, findings, {"idle": True})
    assert related == ["IDLE_HUNT"] and gaps == []
    out = symptoms.reorder(findings, related)
    assert [f.id for f in out] == ["IDLE_HUNT", "LEAN_CRUISE", "LOW_VOLTAGE"]
    # uncovered region -> a concrete capture-this gap, in plain English
    related2, gaps2 = symptoms.relate(symptoms.match("bogs when I floor it"),
                                      findings, {"wot": False})
    assert len(gaps2) == 1 and "re-analyze" in gaps2[0]


def test_complaint_flows_through_analyze_log():
    # ride42 is a drive log with idle-quality findings; a rough-idle complaint
    # should pin them first and land in the to_dict contract.
    cr = analyze_log(os.path.join(FIX, "ride42.csv"),
                     _opts(complaint="idles rough and surges at stoplights"),
                     out_dir=None)
    d = cr.to_dict()
    c = d["complaint"]
    assert any(m["id"] == "rough_idle" for m in c["matched"])
    assert c["related_ids"], "ride42 has idle findings; complaint should relate"
    # the related findings are pinned to the FRONT of the list
    top = [f["id"] for f in d["findings"][:len(c["related_ids"])]]
    assert set(top) == set(c["related_ids"])


def test_complaint_gap_when_log_lacks_the_region():
    # a WOT complaint on a log that never leaves idle -> honest coverage gap
    n = 2000
    df = pd.DataFrame({"Time": np.arange(n) * .05, "Engine RPM": np.full(n, 800.0),
                       "Intake Manifold Absolute Pressure": np.full(n, 35.0),
                       "Throttle Position": np.zeros(n),
                       "Coolant Temp": np.full(n, 195.0),
                       "Short Term Fuel Trim Bank 1": np.zeros(n),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "idle.csv")
        df.to_csv(p, index=False)
        cr = analyze_log(p, _opts(complaint="bogs when I floor it"), out_dir=None)
    c = cr.to_dict()["complaint"]
    assert any(m["id"] == "hesitation" for m in c["matched"])
    assert c["gaps"] and "full pull" in c["gaps"][0]


def test_unrecognized_complaint_is_honest_and_harmless():
    cr = analyze_log(os.path.join(FIX, "ride42.csv"),
                     _opts(complaint="the paint is peeling off the hood"), out_dir=None)
    c = cr.to_dict()["complaint"]
    assert c["matched"] == [] and c["related_ids"] == [] and c["gaps"] == []
    # ...and the analysis itself is untouched
    base = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(), out_dir=None)
    assert [f.id for f in cr.findings] == [f.id for f in base.findings]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all symptoms tests passed")
