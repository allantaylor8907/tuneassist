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


def test_trim_clipping():
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 24.0      # pegged near +/-25 authority
    df["Long Term Fuel Trim Bank 1"] = 0.0
    assert "TRIM_CLIPPING" in _ids(_diag(df))


def test_trim_clipping_one_bank_is_bank_specific():
    # only bank 2 pegged -> point at that bank's O2 / exhaust leak / injectors,
    # NOT a global fuel-supply cause (HPTuners 'LTFT bank 2 = 100')
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 3.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    df["Short Term Fuel Trim Bank 2"] = 24.0
    df["Long Term Fuel Trim Bank 2"] = 0.0
    f = [x for x in _diag(df) if x.id == "TRIM_CLIPPING"][0]
    assert "Bank 2" in f.title
    blob = " ".join(f.causes + f.corrections).lower()
    assert "o2" in blob and "exhaust leak" in blob


def test_trim_clipping_both_banks_is_global():
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 24.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    df["Short Term Fuel Trim Bank 2"] = 23.0
    df["Long Term Fuel Trim Bank 2"] = 0.0
    f = [x for x in _diag(df) if x.id == "TRIM_CLIPPING"][0]
    assert "Both banks" in f.title
    assert any("fuel supply" in c.lower() or "fuel pressure" in c.lower()
               for c in f.causes + f.corrections)


def test_timing_below_command_needs_both_channels():
    # only ACTUAL spark logged (no commanded/desired) -> we never read the tune,
    # so we cannot compute a commanded-vs-actual delta and must stay silent
    df = _base()
    df["Spark Advance"] = 22.0
    assert "TIMING_BELOW_COMMAND" not in _ids(_diag(df))


def test_timing_below_command_attributes_to_knock():
    df = _base()
    df["Commanded Spark Advance"] = 24.0
    df["Spark Advance"] = 17.0           # ~7 deg below command
    df["Knock Retard"] = 6.0             # ...explained by knock
    f = [x for x in _diag(df) if x.id == "TIMING_BELOW_COMMAND"][0]
    assert "below commanded" in f.title.lower()
    assert any("knock" in c.lower() for c in f.causes)


def test_timing_below_command_without_knock_points_at_blend():
    df = _base()
    df["Commanded Spark Advance"] = 24.0
    df["Spark Advance"] = 18.0           # below command but NO knock, cool IAT
    df["Knock Retard"] = 0.0
    df["Intake Air Temp"] = 80.0
    f = [x for x in _diag(df) if x.id == "TIMING_BELOW_COMMAND"][0]
    blob = " ".join(f.causes + f.corrections).lower()
    assert "octane" in blob or "blend" in blob


def test_logging_tips_nudges_missing_channels():
    # a sparse GM log (no knock/wideband/IAT) -> coach lists what to add
    f = [x for x in _diag(_base()) if x.id == "LOGGING_TIPS"]
    assert f, "expected a LOGGING_TIPS nudge on a sparse log"
    blob = " ".join(f[0].corrections).lower()
    assert "knock" in blob and "wideband" in blob


def test_logging_tips_are_stage_gated():
    # well-instrumented EXCEPT commanded-timing and MAF-frequency, so those two
    # are the only nudges -- isolating the stage gate. Commanded-timing only
    # matters at the spark stage; MAF-Hz once airflow tuning is near.
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 2.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    df["Short Term Fuel Trim Bank 2"] = 2.0
    df["Long Term Fuel Trim Bank 2"] = 0.0
    df["Knock Retard"] = 0.0
    df["Wideband AFR"] = 14.7
    df["Intake Air Temp"] = 90.0
    df["Fuel Pressure"] = 58.0
    df["Spark Advance"] = 22.0           # actual, but no commanded PID
    df["Mass Air Flow"] = 8.0            # has MAF but no frequency channel

    def tips(stage):
        f = [x for x in diagnose(df, resolve_columns(df), Config(), stage=stage)
             if x.id == "LOGGING_TIPS"]
        return " ".join(f[0].corrections).lower() if f else ""

    early = tips("STABILIZE_IDLE")       # too soon for either
    assert "commanded" not in early and "maf frequency" not in early
    near_maf = tips("TUNE_VE_SD")        # next stage is TUNE_MAF
    assert "maf frequency" in near_maf and "commanded" not in near_maf
    spark = tips("TUNE_POWER")           # next stage is TUNE_SPARK
    assert "commanded" in spark


def test_logging_tips_suggests_map_when_absent():
    df = _base().drop(columns=["MAP"])
    df["Short Term Fuel Trim Bank 1"] = 2.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    f = [x for x in diagnose(df, resolve_columns(df), Config(), stage="DIAL_IDLE_CRUISE")
         if x.id == "LOGGING_TIPS"]
    assert f and any("intake map" in c.lower() for c in f[0].corrections)


def test_logging_tips_silent_when_well_instrumented():
    # everything the coach would ask for is present -> no nudge
    df = _base()
    df["Short Term Fuel Trim Bank 1"] = 2.0
    df["Long Term Fuel Trim Bank 1"] = 0.0
    df["Short Term Fuel Trim Bank 2"] = 2.0
    df["Long Term Fuel Trim Bank 2"] = 0.0
    df["Knock Retard"] = 0.0
    df["Wideband AFR"] = 14.7
    df["Intake Air Temp"] = 90.0
    df["Fuel Pressure"] = 58.0
    df["Spark Advance"] = 22.0
    df["Commanded Spark Advance"] = 22.0
    assert "LOGGING_TIPS" not in _ids(_diag(df))


def test_low_voltage():
    df = _base()
    df["Battery Voltage"] = 12.1
    assert "LOW_VOLTAGE" in _ids(_diag(df))


def test_low_voltage_ignores_keyoff_reads():
    import numpy as np
    df = _base(n=600)
    v = np.full(600, 14.1); v[:50] = 0.0          # a few key-off/bad reads
    df["Battery Voltage"] = v
    assert "LOW_VOLTAGE" not in _ids(_diag(df))


def test_low_fuel_pressure():
    df = _base()
    df["Fuel Pressure"] = 33.0
    assert "LOW_FUEL_PRESSURE" in _ids(_diag(df))


def test_normal_voltage_and_pressure_clean():
    df = _base()
    df["Battery Voltage"] = 14.1
    df["Fuel Pressure"] = 58.0
    assert not (_ids(_diag(df)) & {"LOW_VOLTAGE", "LOW_FUEL_PRESSURE", "TRIM_CLIPPING"})


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


# ---- transmission ----
def test_trans_wot_shifts_early():
    # WOT upshifts at ~4500 while the engine revs to 6200 -> early
    rpm, gear, tps = [], [], []
    cur = 1
    for _ in range(4):
        for r in range(3000, 4600, 100):
            rpm.append(r); gear.append(cur); tps.append(90)
        cur += 1
        for r in range(2800, 3200, 100):
            rpm.append(r); gear.append(cur); tps.append(90)
    for r in range(4000, 6300, 100):                 # one pull to 6200
        rpm.append(r); gear.append(1); tps.append(95)
    n = len(rpm)
    df = pd.DataFrame({"Time": np.arange(n) * 0.1, "Engine RPM": rpm, "MAP": np.full(n, 95.0),
                       "Throttle Position": tps, "Coolant Temp": np.full(n, 195.0), "Gear": gear})
    assert "TRANS_SHIFT_EARLY" in _ids(_diag(df))


def test_tcc_slip_with_live_iss():
    n = 1200
    rpm = np.full(n, 2200.0)
    iss = np.full(n, 1700.0)            # live, but ~500 below engine -> slipping
    df = pd.DataFrame({"Engine RPM": rpm, "Input Shaft Speed": iss, "MAP": np.full(n, 45.0),
                       "Throttle Position": np.full(n, 15.0), "Coolant Temp": np.full(n, 195.0),
                       "TCC Lockup": np.full(n, 1.0)})       # commanded locked
    f = [x for x in _diag(df) if x.id == "TCC_SLIP"]
    assert f and f[0].severity == "warning"


def test_dead_iss_does_not_false_flag():
    # ISS present but reads 0 (not wired), TCC always 0 (no lockup) -> stay silent
    n = 1200
    df = pd.DataFrame({"Engine RPM": np.full(n, 2200.0), "Input Shaft Speed": np.zeros(n),
                       "MAP": np.full(n, 45.0), "Throttle Position": np.full(n, 15.0),
                       "Coolant Temp": np.full(n, 195.0), "TCC Lockup": np.zeros(n)})
    ids = _ids(_diag(df))
    assert not (ids & {"TCC_SLIP", "TCC_NOT_LOCKING"})


def test_line_pressure_flat():
    n = 1200
    rpm = np.r_[np.full(600, 800.0), np.full(600, 5000.0)]
    tps = np.r_[np.full(600, 4.0), np.full(600, 90.0)]
    lp = np.full(n, 120.0)              # same at idle and WOT -> doesn't rise
    df = pd.DataFrame({"Engine RPM": rpm, "Throttle Position": tps, "MAP": np.full(n, 60.0),
                       "Coolant Temp": np.full(n, 195.0), "Gear": np.r_[np.full(600, 1.0), np.full(600, 2.0)],
                       "Line Pressure": lp})
    assert "LINE_PRESSURE_FLAT" in _ids(_diag(df))


def test_dead_line_pressure_channel_does_not_flag():
    # Line Pressure present but reads 0 (no sensor wired) -> must NOT flag flat
    n = 1200
    rpm = np.r_[np.full(600, 800.0), np.full(600, 5000.0)]
    tps = np.r_[np.full(600, 4.0), np.full(600, 90.0)]
    df = pd.DataFrame({"Engine RPM": rpm, "Throttle Position": tps, "MAP": np.full(n, 60.0),
                       "Coolant Temp": np.full(n, 195.0),
                       "Gear": np.r_[np.full(600, 1.0), np.full(600, 2.0)],
                       "Line Pressure": np.zeros(n)})
    assert "LINE_PRESSURE_FLAT" not in _ids(_diag(df))


# ---- cold start / warmup ----
def _warmup_log(warmup_afr=14.7, ect_max=190.0, dur_s=400.0, ase_warm=0.0,
                ase_neutral=0.0, n=2000):
    t = np.linspace(0, dur_s, n)
    ect = np.clip(70 + (ect_max - 70) * (t / (dur_s * 0.6)), 70, ect_max)
    afr = np.where(ect < 160, warmup_afr, 14.7)
    ase = np.where(ect < 160, ase_warm + 30, ase_neutral) if ase_warm or ase_neutral else None
    d = {"Time": t, "Engine RPM": np.full(n, 1100.0), "MAP": np.full(n, 45.0),
         "Throttle Position": np.full(n, 4.0), "Coolant Temp": ect, "Wideband AFR": afr}
    # afterstart enrichment that stays elevated when warm (ase_warm above neutral)
    if ase_warm:
        d["Afterstart Enr"] = np.where(ect < 160, ase_neutral + 30, ase_neutral + ase_warm)
    return pd.DataFrame(d)


def test_thermostat_never_reaches_temp():
    assert "THERMOSTAT" in _ids(_diag(_warmup_log(ect_max=150.0, dur_s=400.0)))


def test_warm_engine_no_thermostat_flag():
    assert "THERMOSTAT" not in _ids(_diag(_warmup_log(ect_max=195.0)))


def test_warmup_rich():
    assert "WARMUP_RICH" in _ids(_diag(_warmup_log(warmup_afr=11.3)))


def test_warmup_lean():
    assert "WARMUP_LEAN" in _ids(_diag(_warmup_log(warmup_afr=16.0)))


def test_enrichment_not_decayed_100_based_neutral():
    # 100% = neutral (Holley): warm value ~100 must NOT flag; ~130 must flag
    ok = _warmup_log(ase_warm=0, ase_neutral=100)     # warm settles at 100 (neutral)
    assert "ENRICH_NOT_DECAYED" not in _ids(_diag(ok))
    bad = _warmup_log(ase_warm=30, ase_neutral=100)   # warm stuck at 130
    assert "ENRICH_NOT_DECAYED" in _ids(_diag(bad))


# ---- startup flare / idle settle ----
def _startup_log(peak=1800.0, settled=900.0, settle_s=10.0, n=2000):
    t = np.arange(n) * 0.05
    rpm = np.full(n, settled)
    rpm[t < 5] = 0.0
    rpm[(t >= 4.9) & (t < 5.0)] = 300.0
    after = t >= 5.0
    x = t[after] - 5.0
    rpm[after] = settled + (peak - settled) * np.exp(-x / (settle_s / 3.0))
    return pd.DataFrame({"Time": t, "Engine RPM": rpm, "MAP": np.full(n, 45.0),
                         "Throttle Position": np.full(n, 4.0), "Coolant Temp": np.full(n, 195.0)})


def test_startup_flare_detected_and_not_critical():
    f = [x for x in _diag(_startup_log(peak=1800, settled=900)) if x.id == "STARTUP_FLARE"]
    assert f and f[0].severity in ("warning", "info")   # never 'critical'
    assert any("Cranking Airflow" in c or "startup airflow" in c.lower()
               for c in f[0].corrections)


def test_no_startup_flare_when_log_starts_running():
    # a mid-drive log (no crank-from-off) must NOT report a startup
    n = 1000
    df = pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": np.full(n, 2200.0),
                       "MAP": np.full(n, 45.0), "Throttle Position": np.full(n, 25.0),
                       "Coolant Temp": np.full(n, 195.0)})
    assert "STARTUP_FLARE" not in _ids(_diag(df))


def test_clean_startup_no_flare():
    # a tidy startup (small overshoot, quick settle) should not flag
    assert "STARTUP_FLARE" not in _ids(_diag(_startup_log(peak=1100, settled=900, settle_s=4)))


# ---- idle quality ----
def _idle_log(idle_rpm=750.0, std=20.0, target=750.0, afr=14.5, iac=25.0,
              timing_std=1.0, n=1200):
    rng = np.random.default_rng(0)
    rpm = np.clip(idle_rpm + rng.normal(0, std, n), 500, 1300)
    return pd.DataFrame({
        "Engine RPM": rpm, "MAP": np.full(n, 42.0), "Throttle Position": np.full(n, 4.0),
        "Coolant Temp": np.full(n, 195.0), "Wideband AFR": np.full(n, afr),
        "Desired Idle Speed": np.full(n, target),
        "Idle Air Control Position": np.full(n, iac),
        "Timing Advance": 20 + rng.normal(0, timing_std, n)})


def test_idle_hunt():
    assert "IDLE_HUNT" in _ids(_diag(_idle_log(std=140.0)))


def test_idle_high_vs_target():
    assert "IDLE_HIGH" in _ids(_diag(_idle_log(idle_rpm=1000.0, target=750.0)))


def test_idle_high_inferred_from_cam_without_target():
    # no logged idle target: a stock-cam build idling at 1050 is high (expected ~600)
    import numpy as np
    from tuneassist.diagnostics import diagnose
    rng = np.random.default_rng(0)
    rpm = np.clip(1050 + rng.normal(0, 20, 1200), 500, 1300)
    df = pd.DataFrame({"Engine RPM": rpm, "MAP": np.full(1200, 45.0),
                       "Throttle Position": np.full(1200, 4.0), "Coolant Temp": np.full(1200, 195.0)})
    ids = {f.id for f in diagnose(df, resolve_columns(df), Config(), cam_class="stock")}
    assert "IDLE_HIGH" in ids
    # without cam info there's no logged target either -> no inferred guess (no false flag)
    ids2 = {f.id for f in diagnose(df, resolve_columns(df), Config())}
    assert "IDLE_HIGH" not in ids2


def test_idle_low_vs_target():
    assert "IDLE_LOW" in _ids(_diag(_idle_log(idle_rpm=560.0, target=800.0)))


def test_idle_lean_and_rich():
    assert "IDLE_LEAN" in _ids(_diag(_idle_log(afr=15.8)))
    assert "IDLE_RICH" in _ids(_diag(_idle_log(afr=12.6)))


def test_iac_closed_at_idle():
    assert "IAC_CLOSED" in _ids(_diag(_idle_log(iac=1.0)))


def test_clean_warm_idle_has_no_idle_warnings():
    ids = _ids(_diag(_idle_log()))   # steady, on-target, ~14.5 AFR, IAC open
    assert not (ids & {"IDLE_HUNT", "IDLE_HIGH", "IDLE_LOW", "IDLE_LEAN"})


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


def _raf_idle_log(n=300, **extra):
    import pandas as pd, numpy as np
    df = pd.DataFrame({"Engine RPM": np.full(n, 800.0), "MAP": np.full(n, 45.0),
                       "Throttle Position": np.zeros(n), "Coolant Temp": np.full(n, 195.0),
                       "Vehicle Speed": np.zeros(n)})
    for k, v in extra.items():
        df[k] = np.full(n, v)
    return df


def test_idle_airflow_correction_flags_base_airflow_low():
    # PCM ADDING idle airflow (RAFPN +) -> base running airflow is too LOW
    f = [x for x in _diag(_raf_idle_log(RAFPN=2.6)) if x.id == "IDLE_AIRFLOW_OFF"][0]
    assert "park/neutral" in f.title
    assert "ADDING" in f.detail and "too LOW" in f.detail


def test_idle_airflow_correction_in_gear_high():
    f = [x for x in _diag(_raf_idle_log(RAFIG=-3.0)) if x.id == "IDLE_AIRFLOW_OFF"][0]
    assert "in-gear" in f.title and "REMOVING" in f.detail and "too HIGH" in f.detail


def test_idle_airflow_small_correction_is_quiet():
    assert "IDLE_AIRFLOW_OFF" not in _ids(_diag(_raf_idle_log(RAFPN=0.5)))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all diagnostics tests passed")
