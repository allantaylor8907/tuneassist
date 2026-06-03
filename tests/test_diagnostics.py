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


def test_vacuum_leak_idle_only():
    n = 800
    rpm = np.r_[np.full(400, 850.0), np.full(400, 2200.0)]   # idle then cruise
    tps = np.r_[np.full(400, 3.0), np.full(400, 25.0)]
    mapk = np.r_[np.full(400, 35.0), np.full(400, 50.0)]
    df = pd.DataFrame({"Engine RPM": rpm, "MAP": mapk, "Throttle Position": tps,
                       "Coolant Temp": np.full(n, 195.0),
                       "Short Term Fuel Trim Bank 1": np.r_[np.full(400, 14.0), np.full(400, 3.0)],
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    ids = _ids(_diag(df))
    assert "VACUUM_LEAK" in ids


def test_wideband_vs_commanded():
    df = _base()
    df["Wideband AFR"] = 15.3
    df["Air-Fuel Ratio Commanded"] = 14.7
    assert "WB_VS_NB" in _ids(_diag(df))


def test_wot_lean_is_critical():
    df = _base(mapk=95.0, tps=98.0)
    df["Wideband AFR"] = 13.6
    df["Air-Fuel Ratio Commanded"] = 12.6
    f = [x for x in _diag(df) if x.id == "WOT_LEAN"]
    assert f and f[0].severity == "critical"


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
