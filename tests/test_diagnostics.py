"""Tests for diagnostics.py -- each builds a synthetic log that should trip one
detector, and asserts the right Finding (and severity) comes out."""
import sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist.engine_gm import Config, resolve_columns
from tuneassist.diagnostics import diagnose, DiagnosticConfig


def _diag(df):
    return diagnose(df, resolve_columns(df), Config())


def _ids(findings):
    return {f.id for f in findings}


def _base(n=600, rpm=2000.0, mapk=45.0, tps=25.0, ect=195.0):
    return pd.DataFrame({
        "Engine RPM": np.full(n, rpm), "MAP": np.full(n, mapk),
        "Throttle Position": np.full(n, tps), "Coolant Temp": np.full(n, ect)})


def test_lean_cruise():
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 8.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    assert "LEAN_CRUISE" in _ids(_diag(df))


def test_rich_cruise():
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = -8.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    assert "RICH_CRUISE" in _ids(_diag(df))


def test_bank_imbalance():
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 8.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    df["Short Term Fuel Trim Bank 2"] = -3.0
    df["Long Term Fuel Trim Bank 2"] = 0.0
    assert "BANK_IMBALANCE" in _ids(_diag(df))


def _leak_log(idle_trim=14.0, cruise_trim=2.0, idle_afr=14.7, n=900):
    """Idle band (~820 rpm), light cruise (~2100), mid (~2600)."""
    rpm = np.r_[np.full(300, 820.0), np.full(300, 2100.0), np.full(300, 2600.0)]
    mapk = np.r_[np.full(300, 42.0), np.full(300, 45.0), np.full(300, 62.0)]
    tps = np.r_[np.full(300, 4.0), np.full(300, 22.0), np.full(300, 40.0)]
    trim = np.r_[np.full(300, idle_trim), np.full(300, cruise_trim), np.full(300, cruise_trim - 1)]
    return pd.DataFrame({
        "Engine RPM": rpm, "MAP": mapk, "Throttle Position": tps,
        "Coolant Temp": np.full(n, 195.0),
        "Short Term Fuel Trim Bank 1": trim, "Long Term Fuel Trim Bank 1": np.zeros(n),
        "Wideband AFR": np.r_[np.full(300, idle_afr), np.full(600, 14.7)]})


def test_vacuum_leak_tapers_with_load():
    # high idle add that tapers as airflow rises = leak signature
    assert "VACUUM_LEAK" in _ids(_diag(_leak_log(idle_trim=14, cruise_trim=2)))


def test_vacuum_leak_lean_idle_is_high_confidence():
    f = [x for x in _diag(_leak_log(idle_trim=18, cruise_trim=3, idle_afr=15.8))
         if x.id == "VACUUM_LEAK"]
    assert f and f[0].confidence == "high"


def test_no_taper_is_not_a_leak():
    # uniformly lean (idle ≈ cruise) is a VE/scaling issue, not a leak
    assert "VACUUM_LEAK" not in _ids(_diag(_leak_log(idle_trim=9, cruise_trim=8)))


def test_uniform_rich_is_not_a_leak():
    assert "VACUUM_LEAK" not in _ids(_diag(_leak_log(idle_trim=-8, cruise_trim=-8)))


def test_big_cam_softens_leak_confidence():
    from tuneassist.diagnostics import diagnose
    df = _leak_log(idle_trim=14, cruise_trim=2)
    f = [x for x in diagnose(df, resolve_columns(df), Config(), cam_class="big")
         if x.id == "VACUUM_LEAK"]
    assert f and f[0].confidence == "low"
    assert any("cam" in c.lower() for c in f[0].causes)


def test_vacuum_leak_detected_on_holley_clcomp():
    from tuneassist import holley
    from tuneassist.diagnostics import diagnose
    n = 900
    rpm = np.r_[np.full(300, 820.0), np.full(300, 2100.0), np.full(300, 2600.0)]
    mapk = np.r_[np.full(300, 42.0), np.full(300, 45.0), np.full(300, 62.0)]
    tps = np.r_[np.full(300, 4.0), np.full(300, 22.0), np.full(300, 40.0)]
    df = pd.DataFrame({"RPM": rpm, "MAP": mapk, "TPS": tps, "CTS": np.full(n, 195.0),
                       "CL Comp": np.r_[np.full(300, 13.0), np.full(600, 2.0)],
                       "Current Learn": np.zeros(n), "AFR": np.full(n, 14.0)})
    ids = {f.id for f in diagnose(df, holley.resolve_holley(df), Config(), platform="holley")}
    assert "VACUUM_LEAK" in ids


def test_wideband_vs_commanded():
    df = _base()
    df["Wideband AFR"] = 15.3
    df["Air-Fuel Ratio Commanded"] = 14.7
    assert "WB_VS_NB" in _ids(_diag(df))


def test_wot_shortfall_is_critical():
    # commanded 12.6 but measured 13.6 -> fuel system isn't delivering (shortfall)
    df = _base(mapk=95.0, tps=98.0)
    df["Wideband AFR"] = 13.6
    df["Air-Fuel Ratio Commanded"] = 12.6
    f = [x for x in _diag(df) if x.id == "WOT_SHORTFALL"]
    assert f and f[0].severity == "critical"


def test_wot_lean_no_command_is_critical():
    # no commanded channel, absolute lean at WOT -> critical
    df = _base(mapk=95.0, tps=98.0)
    df["Wideband AFR"] = 13.7
    f = [x for x in _diag(df) if x.id == "WOT_LEAN"]
    assert f and f[0].severity == "critical"


def test_wot_hitting_lean_target_is_opportunity_not_lean():
    # measured == commanded at a lean-ish target -> power opportunity, not 'lean'
    df = _base(mapk=95.0, tps=98.0)
    df["Wideband AFR"] = 13.2
    df["Air-Fuel Ratio Commanded"] = 13.2
    ids = {f.id for f in _diag(df)}
    assert "WOT_TARGET_LEAN" in ids and "WOT_LEAN" not in ids
    assert not [f for f in _diag(df) if f.severity == "critical"]


def test_wot_rich_is_opportunity():
    df = _base(mapk=95.0, tps=98.0)
    df["Wideband AFR"] = 11.7
    df["Air-Fuel Ratio Commanded"] = 11.7
    f = [x for x in _diag(df) if x.id == "WOT_RICH"]
    assert f and f[0].severity == "opportunity"


def test_injector_duty_limit():
    n = 600
    rpm = np.full(n, 6000.0)
    df = pd.DataFrame({"Engine RPM": rpm, "MAP": np.full(n, 95.0),
                       "Coolant Temp": np.full(n, 195.0),
                       "Injector Pulse Width Avg": np.full(n, 20.0)})   # 20*6000/1200 = 100%
    f = [x for x in _diag(df) if x.id == "INJ_DUTY"]
    assert f and f[0].severity == "critical"


def test_knock_detected():
    df = _base(mapk=95.0)
    df["Knock Retard"] = np.r_[np.full(300, 0.0), np.full(300, 3.0)]
    assert "KNOCK" in _ids(_diag(df))


def test_overheat_and_high_iat():
    df = _base()
    df["Coolant Temp"] = np.r_[np.full(300, 200.0), np.full(300, 245.0)]
    df["Intake Air Temp"] = 160.0
    ids = _ids(_diag(df))
    assert "OVERHEAT" in ids and "HIGH_IAT" in ids


def test_trim_oscillation():
    rng = np.random.default_rng(0)
    df = _base(n=400)
    df["Short Term Fuel Trim Bank 1"] = rng.normal(0, 14, 400)
    df["Long Term Fuel Trim Bank 1"] = 0.0
    assert "TRIM_OSCILLATION" in _ids(_diag(df))


def _boosted(afr_boost=11.5, fp_boost=58.0, iat_boost=110.0, n=1500):
    rpm = np.clip(3000 + 1800 * np.sin(np.arange(n) / 30), 900, 6500)
    mapk = np.clip(60 + 90 * np.sin(np.arange(n) / 24), 25, 180)
    b = mapk > 105
    return pd.DataFrame({
        "Engine RPM": rpm, "MAP": mapk, "Throttle Position": np.clip(mapk - 20, 0, 100),
        "Coolant Temp": np.full(n, 195.0), "Intake Air Temp": np.where(b, iat_boost, 95.0),
        "Wideband AFR": np.where(b, afr_boost, 14.5), "Baro": np.full(n, 101.0),
        "Fuel Pres": np.where(b, fp_boost, 58.0)})


def _diag_boost(df, profile=None):
    from tuneassist.profile import EngineProfile
    return diagnose(df, resolve_columns(df), Config(),
                    profile=profile or EngineProfile(power_adder="boost"))


def test_forced_induction_detected():
    assert "FORCED_INDUCTION" in _ids(_diag_boost(_boosted()))


def test_boost_lean_is_critical():
    f = [x for x in _diag_boost(_boosted(afr_boost=12.6)) if x.id == "BOOST_LEAN"]
    assert f and f[0].severity == "critical"


def test_fuel_pressure_drop_under_boost():
    f = [x for x in _diag_boost(_boosted(fp_boost=40.0)) if x.id == "FUEL_PRESSURE_DROP"]
    assert f and f[0].severity == "critical"


def test_boost_iat_warns():
    assert "BOOST_IAT" in _ids(_diag_boost(_boosted(iat_boost=165.0)))


def test_map_sensor_cant_read_boost():
    # profile says boost, but MAP never clears ~1 bar -> 1-bar sensor blind
    n = 600
    df = pd.DataFrame({"Engine RPM": np.full(n, 5000.0), "MAP": np.full(n, 102.0),
                       "Throttle Position": np.full(n, 95.0), "Coolant Temp": np.full(n, 195.0)})
    assert "MAP_SENSOR_RANGE" in _ids(_diag_boost(df))


def test_na_log_has_no_boost_findings():
    df = _base(mapk=95.0)        # never above ~1 bar
    df["Wideband AFR"] = 13.0
    ids = _ids(_diag(df))
    assert "FORCED_INDUCTION" not in ids and "BOOST_LEAN" not in ids


def test_clean_log_has_no_critical_findings():
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 1.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    assert not [f for f in _diag(df) if f.severity == "critical"]


def test_findings_sorted_critical_first():
    df = _base(mapk=95.0, tps=98.0)
    df["Wideband AFR"] = 13.6                  # WOT lean (critical)
    df["Air-Fuel Ratio Commanded"] = 12.6
    df["Intake Air Temp"] = 160.0              # high IAT (info)
    findings = _diag(df)
    assert findings[0].severity == "critical"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all diagnostics tests passed")
