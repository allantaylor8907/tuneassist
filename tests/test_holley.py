"""Regression tests on a REAL Holley Terminator X CSV export (tests/fixtures/
holley_sample.csv). This is the first real-data Holley coverage -- it pins the
loader (units row, latin-1 degree sign), channel resolution (MAP vs 'MAP RoC',
time=RTC, knock), the Learn-based base-fuel correction, and platform-correct
diagnosis (no narrowband finding; a power opportunity instead of a false lean)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist import holley
from tuneassist.core import analyze_log, SessionOpts, detect_platform
from tuneassist.engine_gm import Config

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "holley_sample.csv")


def test_loads_and_skips_units_row():
    df = holley.load_holley_csv(FIX)
    assert df.shape[0] > 300            # data rows, units row dropped
    # RPM column is numeric (units row didn't leak in as a string row)
    import pandas as pd
    assert pd.to_numeric(df["RPM"], errors="coerce").notna().mean() > 0.95


def test_channels_resolve_to_the_right_columns():
    col = holley.resolve_holley(holley.load_holley_csv(FIX))
    assert col["rpm"] == "RPM"
    assert col["map"] == "MAP"              # not 'MAP RoC'
    assert col["tps"] == "TPS"              # not 'TPS RoC'
    assert col["time"] == "RTC"
    assert col["afr_actual"] == "AFR" and col["afr_target"] == "Target AFR"
    assert col["learn"] == "Current Learn" and col["cl_comp"] == "CL Comp"
    assert col["knock"] == "Knock Retard"
    assert col["cts"] == "CTS" and col["mat"] == "MAT"


def test_detected_as_holley():
    assert detect_platform(FIX) == "holley"


def test_base_fuel_correction_reflects_learn():
    cr = analyze_log(FIX, SessionOpts(cfg=Config(), airflow_mode="no_maf"))
    assert cr.platform == "holley"
    assert cr.triage.state == "RUNNING_DRIVE"
    assert cr.has_grid
    # the ECU learned a big negative correction; we should recover roughly that
    assert -28 < cr.summary.median_pct < -8


def test_holley_diagnosis_is_platform_correct():
    cr = analyze_log(FIX, SessionOpts(cfg=Config(), tune_spark=True))
    ids = {f.id for f in cr.findings}
    # the wideband IS the controller on Holley -> no "lying narrowband" finding
    assert "WB_VS_NB" not in ids
    # hitting target at WOT must NOT be a false 'critical lean'
    assert not [f for f in cr.findings if f.id == "WOT_LEAN"]
    assert not [f for f in cr.findings if f.severity == "critical"]
    # the lean-ish WOT target should surface as a power opportunity
    assert "WOT_TARGET_LEAN" in ids


def test_prescription_uses_holley_language():
    cr = analyze_log(FIX, SessionOpts(cfg=Config(), airflow_mode="no_maf"))
    rx = cr.prescription
    blob = (rx.title + " " + rx.rationale + " " + " ".join(rx.actions)).lower()
    assert "base fuel" in blob
    assert "speed-density" not in blob and "maf" not in blob   # no GM jargon


SNIPER = os.path.join(os.path.dirname(__file__), "fixtures", "sniper_sample.csv")


def test_sniper_v2_resolves_and_avoids_traps():
    col = holley.resolve_holley(holley.load_holley_csv(SNIPER))
    assert col["rpm"] == "RPM" and col["map"] == "MAP" and col["afr_actual"] == "AFR"
    assert col["afr_target"] == "Target AFR" and col["learn"] == "Current Learn"
    assert col["battery"] == "Battery" and col["speed"] == "Speed"
    # 'Fuel Press Switch' is a switch, not a pressure -> must NOT resolve as fuelpres
    assert "fuelpres" not in col
    # Sniper V2 has no knock sensor
    assert "knock" not in col


def test_sniper_v2_detected_and_analyzes():
    cr = analyze_log(SNIPER, SessionOpts(cfg=Config(), tune_spark=True))
    assert cr.platform == "holley"
    assert cr.triage.state == "RUNNING_DRIVE" and cr.has_grid
    # a well-tuned driving log: no false idle-hunt from decel/coast samples,
    # no false low-fuel-pressure from the pressure switch
    ids = {f.id for f in cr.findings}
    assert "IDLE_HUNT" not in ids
    assert "LOW_FUEL_PRESSURE" not in ids
    assert "APPLY_FUEL" in ids        # the base-fuel correction is the lead item


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all holley tests passed")
