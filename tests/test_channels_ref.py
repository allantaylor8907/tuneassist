"""Tests for channels_ref.py -- the logging reference + coverage check."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist import channels_ref


def test_reference_has_every_generation():
    ref = channels_ref.reference()
    for key in ("gm_gen3_ls", "gm_gen4_ls", "gm_gen5_lt", "holley"):
        assert key in ref and ref[key]["label"] and ref[key]["channels"]
        for c in ref[key]["channels"]:
            assert c["name"] and c["tier"] in ("essential", "recommended", "reference")


def test_gen3_vs_gen4_differences():
    g3 = [c["name"] for c in channels_ref.reference()["gm_gen3_ls"]["channels"]]
    g4 = [c["name"] for c in channels_ref.reference()["gm_gen4_ls"]["channels"]]
    assert "Cylinder Airmass" in g3 and "DFCO Status" in g3       # Gen 3-only
    assert "Intake Valve Temp (IVT)" not in g3                    # Gen 4-only
    assert "Intake Valve Temp (IVT)" in g4 and "PE Advance" in g4
    assert "Commanded Equivalence Ratio" in g4                    # Gen 4 is EQ-based


def test_coverage_flags_missing_essentials():
    # a GM Gen 3 log missing knock + wideband
    col = {"rpm": "RPM", "map": "MAP", "tps": "TPS", "ect": "ECT", "iat": "IAT",
           "stft": "STFT", "ltft": "LTFT", "afr_cmd": "Cmd AFR", "maf_freq": "MAF Hz",
           "spark": "Adv"}
    cov = channels_ref.coverage(col, "gm", "gm_gen3_ls")
    missing = {m["name"]: m for m in cov["missing"]}
    assert "Knock Retard" in missing and missing["Knock Retard"]["tier"] == "essential"
    assert cov["ok"] is False                                     # an essential is missing
    assert "Engine RPM" in cov["present"]
    assert all(m["why"] for m in cov["missing"] if m["tier"] != "reference")


def test_coverage_ok_when_essentials_present():
    col = {"rpm": "a", "map": "b", "tps": "c", "ect": "d", "stft": "e", "ltft": "f",
           "afr_cmd": "g", "knock": "h", "iat": "i", "maf_freq": "j",
           "maf_air": "k", "spark": "l", "afr_actual": "m", "airmass": "n"}
    cov = channels_ref.coverage(col, "gm", "gm_gen3_ls")
    assert cov["ok"] is True and cov["n_present"] >= 10


def test_holley_coverage_uses_holley_keys():
    # Holley's resolver uses cts/mat/afr_target/cl_comp/learn, NOT the GM names
    col = {"rpm": "a", "map": "b", "tps": "c", "afr_actual": "d", "afr_target": "e",
           "cts": "f", "cl_comp": "g", "learn": "h", "mat": "i"}
    cov = channels_ref.coverage(col, "holley", "holley_terminator")
    assert cov["ok"] is True
    # an essential expressed in GM terms must NOT be reported missing here
    assert not any(m["name"] == "Coolant Temp (CTS)" for m in cov["missing"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all channels_ref tests passed")
