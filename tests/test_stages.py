"""Tests for the journey state machine (stages.py) -- pure logic, no terminal."""
import sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist.stages import (AnalysisSummary, determine_stage, prescribe,
                               summarize, STAGE_ORDER)
from tuneassist.engine_gm import Config, analyze


def test_non_running_states_map_to_journey():
    empty = AnalysisSummary()
    assert determine_stage("NO_CRANK", empty) == "GET_RUNNING"
    assert determine_stage("CRANKING_NO_START", empty) == "GET_RUNNING"
    assert determine_stage("STARTED_STALLED", empty) == "GET_RUNNING"
    assert determine_stage("UNSTABLE_IDLE", empty) == "STABILIZE_IDLE"
    assert determine_stage("IDLE_ONLY", empty) == "DIAL_IDLE_CRUISE"


def test_running_with_cruise_error_ve_mode_is_tune_ve_sd():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=5.0, max_abs_pct=5.0)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="ve_sd") == "TUNE_VE_SD"


def test_running_with_cruise_error_maf_mode_is_tune_maf():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=5.0, max_abs_pct=5.0)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="maf") == "TUNE_MAF"


def test_maf_mode_systemic_offset_routes_back_to_ve():
    # a big whole-map shift in MAF mode is VE-table error, not MAF-curve work
    # (e.g. the 2004 5.3 log: median ~-8% across the map) -> send back to VE
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=12.0, max_abs_pct=12.0,
                        median_pct=-8.3)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="maf") == "TUNE_VE_SD"


def test_maf_mode_small_residual_stays_maf():
    # centered map with only a couple outlier cells = genuine MAF-curve fine-tuning
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=5.0, max_abs_pct=5.0,
                        median_pct=-0.8)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="maf") == "TUNE_MAF"


def test_ve_sd_converged_moves_to_maf():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=0.5, max_abs_pct=0.5)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="ve_sd") == "TUNE_MAF"


def test_cruise_done_no_wot_is_tune_power():
    # no-MAF setup: after cruise, WOT fuel is next
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=1.0,
                        wot_covered=False, max_abs_pct=1.0)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="no_maf") == "TUNE_POWER"


def test_fuel_done_no_spark_is_converged():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=0.5,
                        wot_covered=True, wot_max_abs_pct=1.0, max_abs_pct=1.2)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="no_maf") == "CONVERGED"


def test_fuel_done_with_spark_work_is_tune_spark():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=0.5,
                        wot_covered=True, wot_max_abs_pct=1.0, max_abs_pct=1.2)
    assert determine_stage("RUNNING_DRIVE", s, airflow_mode="no_maf",
                           tune_spark=True, spark_has_work=True) == "TUNE_SPARK"


def test_running_no_confident_cells_falls_back():
    assert determine_stage("RUNNING_DRIVE", AnalysisSummary()) == "DIAL_IDLE_CRUISE"


def test_journey_is_monotonic_ordering():
    # the ladder must be strictly increasing so the progress bar makes sense
    keys = list(STAGE_ORDER)
    assert STAGE_ORDER[keys[0]] == 0
    assert all(STAGE_ORDER[keys[i]] < STAGE_ORDER[keys[i + 1]]
               for i in range(len(keys) - 1))


def test_prescribe_global_offset_recommends_scalar():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=4.0, max_abs_pct=4.0,
                        offset={"shape": "global_offset", "median_pct": -4.7,
                                "n_cells": 40, "spread_pct": 1.0})
    rx = prescribe("TUNE_VE_SD", s, [], "gm")
    assert any("GLOBAL offset" in a or "scalar" in a for a in rx.actions)


def test_prescribe_table_shape_recommends_table():
    s = AnalysisSummary(n_confident=20, cruise_max_abs_pct=4.0, max_abs_pct=4.0,
                        offset={"shape": "table_shape", "median_pct": -0.8,
                                "n_cells": 34, "spread_pct": 3.7})
    rx = prescribe("TUNE_VE_SD", s, [], "gm")
    assert any("table-SHAPE" in a or "VE table" in a for a in rx.actions)


def test_prescribe_tune_maf_says_reenable_and_hz():
    rx = prescribe("TUNE_MAF", AnalysisSummary(), [], "gm")
    assert any("RE-ENABLE" in a.upper() or "MAF cal" in a or "Hz" in a for a in rx.actions)


def test_prescribe_spark_refuses_without_knock():
    class _NoSpark:
        can_run = False
        reason = "No knock channel"
    rx = prescribe("TUNE_SPARK", AnalysisSummary(), [], "gm", spark=_NoSpark())
    assert "knock" in (rx.rationale + " ".join(rx.actions)).lower()


def test_get_running_uses_triage_recs():
    rx = prescribe("GET_RUNNING", AnalysisSummary(), ["Check crank sensor"], "gm")
    assert rx.actions == ["Check crank sensor"]
    assert "start" in rx.drive.lower()


def _synthetic_running_log(n=1500, trim=4.0):
    """A warm, driven log with a steady positive fuel trim -> a real flat offset."""
    rng = np.random.default_rng(0)
    rpm = np.clip(1800 + 1000 * np.sin(np.arange(n) / 40) + rng.normal(0, 60, n), 700, 4200)
    mapk = np.clip(45 + 30 * np.sin(np.arange(n) / 33), 22, 95)
    return pd.DataFrame({
        "Time": np.arange(n) * 0.05,
        "Engine RPM": rpm,
        "MAP": mapk,
        "TPS": np.clip((mapk - 20) / 78 * 100, 0, 90),
        "Coolant Temp": np.full(n, 195.0),
        "Commanded AFR": np.full(n, 14.7),
        "Short Term Fuel Trim Bank 1": np.full(n, trim) + rng.normal(0, 0.5, n),
        "Long Term Fuel Trim Bank 1": np.zeros(n),
    })


def test_summarize_flat_trim_is_global_offset():
    df = _synthetic_running_log(trim=4.0)
    res = analyze(df, Config())
    s = summarize(res, Config())
    assert s.n_confident > 0
    assert s.offset.get("shape") == "global_offset"
    # +4% trim, 0.70 damping -> ~ +2.8% correction
    assert 2.0 < s.median_pct < 3.5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all stage tests passed")
