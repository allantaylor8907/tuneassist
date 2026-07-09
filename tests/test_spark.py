"""Tests for spark.py (knock-governed timing) and cams.py (starting points)."""
import sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist.engine_gm import Config, maf_correction
from tuneassist.spark import analyze_spark
from tuneassist import cams


def _wot_log(n=1500, knock_at_high_load=3.0, iat=95.0, afr_wot=12.6):
    rng = np.random.default_rng(0)
    rpm = np.clip(3000 + 1500 * np.sin(np.arange(n) / 30), 900, 6000)
    mapk = np.clip(60 + 35 * np.sin(np.arange(n) / 25), 25, 100)
    return pd.DataFrame({
        "Engine RPM": rpm, "MAP": mapk, "TPS": np.clip(mapk - 20, 0, 100),
        "Coolant Temp": np.full(n, 195.0), "IAT": np.full(n, iat),
        "Spark Advance": np.full(n, 24.0),
        "Knock Retard": np.where(mapk > 90, knock_at_high_load, 0.0),
        "Wideband AFR": np.where(mapk > 80, afr_wot, 14.7),
        "MAF Frequency": np.clip(2000 + mapk * 60 + rpm * 2, 1000, 12000),
        "Short Term Fuel Trim Bank 1": np.full(n, 3.0),
        "Long Term Fuel Trim Bank 1": np.zeros(n),
    })


def test_spark_refuses_without_knock_channel():
    df = _wot_log().drop(columns=["Knock Retard"])
    r = analyze_spark(df, Config())
    assert r.can_run is False and "knock" in r.reason.lower()


def test_spark_pulls_where_knock():
    r = analyze_spark(_wot_log(knock_at_high_load=3.0), Config())
    assert r.can_run and r.knock_cells > 0
    acts = r.action.stack().dropna()
    assert (acts == "PULL").any()
    # a pull must be negative and exceed the observed retard (margin added)
    pulls = r.change.where(r.action == "PULL").stack().dropna()
    assert (pulls < 0).all() and pulls.min() <= -(3.0 + Config().knock_pull_margin) + 0.01


def test_spark_flags_lean_knock_as_lean():
    # lean at WOT (15.0) + knock -> should flag LEAN (fix fuel first), not PULL
    r = analyze_spark(_wot_log(afr_wot=15.0), Config())
    assert (r.action.stack().dropna() == "LEAN").any()


def test_spark_flags_hot_iat():
    r = analyze_spark(_wot_log(iat=140.0), Config())
    assert (r.action.stack().dropna() == "HOT").any()


def test_power_adds_computed_in_safe_wot_cells():
    # ADDs are always computed now (the GUI reveals them on demand); find_power is
    # just the default-reveal preference, carried through on the result.
    r = analyze_spark(_wot_log(knock_at_high_load=0.0), Config(), find_power=False)
    assert (r.action.stack().dropna() == "ADD").any()
    assert r.find_power is False
    on = analyze_spark(_wot_log(knock_at_high_load=0.0), Config(), find_power=True)
    assert on.find_power is True


def test_maf_correction_builds_frequency_table():
    corr, counts, notes = maf_correction(_wot_log(), Config())
    assert corr is not None and corr.notna().sum() > 0


def test_maf_correction_needs_frequency_channel():
    corr, counts, notes = maf_correction(_wot_log().drop(columns=["MAF Frequency"]), Config())
    assert corr is None


def test_cam_classification():
    assert cams.classify(cams.CamSpec()) == "unknown"
    assert cams.classify(cams.CamSpec(intake_dur_050=200, exhaust_dur_050=206)) == "stock"
    assert cams.classify(cams.CamSpec(intake_dur_050=232, exhaust_dur_050=240)) == "big"
    # tight LSA bumps a mild cam to "big" behavior at idle
    assert cams.classify(cams.CamSpec(intake_dur_050=220, exhaust_dur_050=224, lsa=110)) == "big"


def test_cam_starting_points_scale_with_size():
    big = cams.starting_points(cams.CamSpec(intake_dur_050=235, exhaust_dur_050=243))
    stock = cams.starting_points(cams.CamSpec(intake_dur_050=200, exhaust_dur_050=206))
    assert big.idle_timing_deg[0] > stock.idle_timing_deg[0]
    assert big.idle_rpm > stock.idle_rpm


def test_profile_iron_low_cr_higher_ceiling_with_heat_warning():
    from tuneassist.profile import EngineProfile, spark_guidance
    adv, pull = spark_guidance(EngineProfile(block="iron", compression=9.5))
    assert "iron block" in adv.lower()
    assert "25-29" in adv or "25-31" in adv          # low CR -> more headroom (E10 default pump)
    assert any("heat-soak" in c.lower() or "consecutive" in c.lower() for c in pull)


def test_profile_high_cr_tightens_ceiling():
    from tuneassist.profile import EngineProfile, spark_guidance
    adv, _ = spark_guidance(EngineProfile(block="alum", compression=11.0))
    assert "22-25" in adv and "high compression" in adv.lower()


def test_profile_boost_overrides_everything():
    from tuneassist.profile import EngineProfile, spark_guidance
    adv, pull = spark_guidance(EngineProfile(block="alum", compression=9.0, power_adder="boost"))
    assert "boost" in adv.lower() and "10-18" in adv
    assert any("boost" in c.lower() for c in pull)


def test_profile_flows_through_spark_result():
    from tuneassist.profile import EngineProfile
    r = analyze_spark(_wot_log(), Config(),
                      profile=EngineProfile(block="iron", compression=9.5))
    assert r.can_run and "iron" in r.advisory.lower() and len(r.pullback) >= 5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all spark/cam tests passed")
