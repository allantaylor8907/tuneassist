"""Tests for fitment.py -- the tree must only ever offer REAL combinations, and
every engine it names must resolve to a profile preset."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist import fitment
from tuneassist.core import ARCHITECTURES
from tuneassist.profile import ENGINE_PRESETS, preset_to_profile

PRESET_LABELS = {lbl for lbl, *_ in ENGINE_PRESETS}


def _all_engines():
    out = []
    for plat, tree in fitment.FITMENT.items():
        for m in tree["makes"]:
            for e in m.get("engines", []):
                out.append((plat, m["key"], None, e))
            for g in m.get("generations", []):
                for e in g["engines"]:
                    out.append((plat, m["key"], g["key"], e))
    return out


def test_every_fitment_engine_is_a_real_preset():
    missing = [e for *_ , e in _all_engines() if e not in PRESET_LABELS]
    assert not missing, f"fitment names engines with no preset: {missing}"


def test_every_generation_is_a_registered_architecture():
    for plat, tree in fitment.FITMENT.items():
        for m in tree["makes"]:
            for g in m.get("generations", []):
                assert g["key"] in ARCHITECTURES, g["key"]
        for pr in tree.get("products", []):
            assert pr["key"] in ARCHITECTURES, pr["key"]


def test_carbed_classics_only_under_holley():
    # an SBC 350 has no factory ECU -- it must not appear under HP Tuners
    hpt_engines = [e for p, m, g, e in _all_engines() if p == "gm"]
    assert not any("SBC" in e or "BBC" in e or "Pontiac" in e for e in hpt_engines)
    holley_engines = [e for p, m, g, e in _all_engines() if p == "holley"]
    assert any("SBC 350" in e for e in holley_engines)


def test_cascade_helpers():
    # HP Tuners -> GM -> Gen 3 -> 6.0 (the user's example) must be reachable
    makes = [m["key"] for m in fitment.makes_for("gm")]
    assert makes == ["gm", "ford", "mopar"]
    gens = [g["key"] for g in fitment.generations_for("gm", "gm")]
    assert gens == ["gm_gen3_ls", "gm_gen4_ls", "gm_gen5_lt"]
    g3 = fitment.engines_for("gm", "gm", "gm_gen3_ls")
    assert "Chevy LS 6.0 LQ4 (iron)" in g3 and "Chevy LS3 6.2 (aluminum)" not in g3
    # Gen 4 list has Gen 4 engines, not Gen 3 exclusives
    g4 = fitment.engines_for("gm", "gm", "gm_gen4_ls")
    assert "Chevy LS3 6.2 (aluminum)" in g4 and "Chevy LS1 5.7 (aluminum)" not in g4
    # auto/unset generation -> union
    assert set(g3) <= set(fitment.engines_for("gm", "gm", None))
    # Holley is flat per make; generations_for is empty
    assert fitment.generations_for("holley", "gm") == []
    assert "Chevy SBC 350 (iron)" in fitment.engines_for("holley", "gm")
    # Mopar on HP Tuners exists (HEMI), with no classics
    hemi = fitment.engines_for("gm", "mopar", "mopar_hemi")
    assert any("HEMI" in e for e in hemi) and not any("440" in e for e in hemi)


def test_every_engine_label_round_trips_to_profile():
    for *_ , e in _all_engines():
        p = preset_to_profile(e)
        assert p is not None and p.displacement and p.block in ("iron", "alum")


def test_power_adder_inference():
    assert fitment.infer_power_adder("Chevy LSA 6.2 supercharged", []) == "boost"
    assert fitment.infer_power_adder("Chevy LS1 5.7 (aluminum)", ["Turbo"]) == "boost"
    assert fitment.infer_power_adder("Chevy LS1 5.7 (aluminum)", ["Nitrous"]) == "nitrous"
    assert fitment.infer_power_adder("Chevy LS1 5.7 (aluminum)", ["Ported heads"]) == "na"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all fitment tests passed")
