"""classify.py -- regex-primary, fuzzy-fallback symptom classifier contract."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tuneassist import classify
from tuneassist.core import analyze_log, SessionOpts
from tuneassist.engine_gm import Config

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def test_pattern_hits_are_authoritative():
    r = classify.classify("idles rough and hunts")
    assert r and r[0]["source"] == "pattern" and r[0]["score"] == 1.0
    assert any(x["id"] == "rough_idle" for x in r)


def test_fuzzy_fallback_only_when_regex_misses():
    # a paraphrase with no regex trigger -> fuzzy, tagged + scored below 1.0
    r = classify.classify("feels gutless and lazy up top")
    assert r and all(x["source"] == "fuzzy" for x in r)
    assert any(x["id"] == "no_power" for x in r)
    assert 0.0 < r[0]["score"] < 1.0
    # unrelated text stays empty (honest)
    assert classify.classify("the paint is peeling off the hood") == []
    assert classify.classify("") == [] and classify.classify(None) == []


def test_graceful_degradation_without_backend():
    # no fallback backend -> regex-only, never an error (the frozen binary must
    # keep working if the model isn't bundled)
    saved = classify._backend
    try:
        classify.set_backend(None)
        assert classify.classify("idles rough and hunts")           # regex still works
        assert classify.classify("feels gutless and lazy up top") == []   # no fuzzy
    finally:
        classify.set_backend(saved)


def test_fuzzy_flag_flows_through_core_to_dict():
    for text, want_fuzzy in [("idles rough and hunts", False),
                             ("feels gutless and lazy up top", True)]:
        cr = analyze_log(os.path.join(FIX, "ride42.csv"),
                         SessionOpts(cfg=Config(), complaint=text), out_dir=None)
        c = cr.to_dict()["complaint"]
        assert c["fuzzy"] is want_fuzzy
        assert all("source" in m and "score" in m for m in c["matched"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all classify tests passed")
