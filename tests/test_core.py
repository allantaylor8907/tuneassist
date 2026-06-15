"""Tests for the headless core (core.py) and its JSON contract.

The JSON shape here IS the contract a future UI (or a Rust/Go port) consumes, so
these tests double as the oracle: if the structure changes, that's a deliberate
decision, not an accident."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist.core import analyze_log, SessionOpts, detect_platform
from tuneassist.engine_gm import Config

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _opts(**kw):
    return SessionOpts(cfg=Config(), **kw)


def test_analyze_log_ride42_structure():
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(airflow_mode="ve_sd"))
    assert cr.platform == "gm"
    assert cr.triage.state == "RUNNING_DRIVE"
    assert cr.stage == "TUNE_VE_SD"
    assert cr.has_grid


def test_to_dict_is_json_serializable_and_complete():
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(tune_spark=True))
    d = cr.to_dict()
    s = json.dumps(d)                     # must not raise
    d2 = json.loads(s)
    # core sections present
    for key in ("platform", "triage", "stage", "summary", "correction", "prescription"):
        assert key in d2
    # correction cells carry rpm/map/percent value
    cell = d2["correction"]["cells"][0]
    assert set(("rpm", "map", "value")) <= set(cell)
    assert d2["correction"]["unit"] == "percent_change"
    # ride42 has a knock channel -> spark runs
    assert d2["spark"]["can_run"] is True
    assert "advisory" in d2["spark"] and isinstance(d2["spark"]["pullback"], list)


def test_json_values_match_known_fixture():
    # oracle: ride42's headline numbers (a port must reproduce these)
    d = analyze_log(os.path.join(FIX, "ride42.csv"), _opts()).to_dict()
    assert d["summary"]["median_pct"] == -0.78
    assert d["summary"]["max_abs_pct"] == 5.91
    assert d["triage"]["state"] == "RUNNING_DRIVE"
    assert d["stage"] == "TUNE_VE_SD"


def test_primary_change_is_the_lead_finding():
    # users must see "apply these changes (and where/how)" up top, not just
    # peripheral issues -- the fuel/VE correction leads the diagnosis.
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts())
    assert cr.findings and cr.findings[0].id == "APPLY_FUEL"
    f = cr.findings[0]
    assert "VE table" in f.title
    joined = " ".join(f.corrections).lower()
    assert "multiply-by-percent" in joined and "main ve table" in joined


def test_apply_finding_states_where_for_holley():
    cr = analyze_log(os.path.join(FIX, "holley_sample.csv"),
                     _opts(airflow_mode="no_maf"))
    f = [x for x in cr.findings if x.id == "APPLY_FUEL"]
    assert f and "base fuel" in " ".join(f[0].corrections).lower()


def test_safety_finding_says_grid_resolves_it():
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts())
    wot = [f for f in cr.findings if f.id in ("WOT_SHORTFALL", "WOT_LEAN")]
    assert wot
    joined = " ".join(wot[0].corrections).lower()
    assert "already covers" in joined or "richen" in joined


def test_safety_finding_flags_hardware_limit():
    # WOT lean WITH injector duty maxed -> the VE change can't fix it
    import numpy as np, pandas as pd
    from tuneassist.core import _annotate_safety_resolution
    from tuneassist.diagnostics import diagnose
    from tuneassist.engine_gm import resolve_columns
    n = 600
    df = pd.DataFrame({"Engine RPM": np.full(n, 6000.0),
                       "Intake Manifold Absolute Pressure": np.full(n, 98.0),
                       "Throttle Position": np.full(n, 98.0), "Coolant Temp": np.full(n, 195.0),
                       "Wideband AFR": np.full(n, 13.6), "Air-Fuel Ratio Commanded": np.full(n, 12.6),
                       "Injector Pulse Width Avg": np.full(n, 20.0)})
    fs = diagnose(df, resolve_columns(df), Config())

    class _S:
        wot_covered = True
    _annotate_safety_resolution(fs, _S(), "gm", "ve_sd")
    wot = [f for f in fs if f.id in ("WOT_SHORTFALL", "WOT_LEAN")]
    assert wot and any("won't be fixed" in c.lower() for c in wot[0].corrections)


def _synth_trim_log(trim_fn, tmp):
    import numpy as np, pandas as pd
    n = 1600
    rpm = np.clip(2200 + 1700 * np.sin(np.arange(n) / 30), 700, 5600)
    mapk = np.clip(50 + 38 * np.sin(np.arange(n) / 25), 22, 98)
    df = pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": rpm,
                       "Intake Manifold Absolute Pressure": mapk,
                       "Throttle Position": np.clip(mapk - 20, 0, 100),
                       "Coolant Temp": np.full(n, 195.0), "Commanded AFR": np.full(n, 14.7),
                       "Short Term Fuel Trim Bank 1": trim_fn(rpm, mapk),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    p = os.path.join(tmp, "synth.csv")
    df.to_csv(p, index=False)
    return p


def test_mod_larger_injectors_explains_flat_offset():
    import numpy as np, tempfile
    from tuneassist.profile import EngineProfile
    with tempfile.TemporaryDirectory() as d:
        p = _synth_trim_log(lambda r, m: np.full(len(r), 5.0), d)  # flat +5%
        cr = analyze_log(p, _opts(profile=EngineProfile(mods=["Larger injectors"])))
    assert cr.summary.offset.get("shape") == "global_offset"
    af = [f for f in cr.findings if f.id == "APPLY_FUEL"][0]
    assert any("larger injectors" in c.lower() and "injector data" in c.lower()
               for c in af.corrections)


def test_mod_airflow_explains_lean_up_top():
    import numpy as np, tempfile
    from tuneassist.profile import EngineProfile
    with tempfile.TemporaryDirectory() as d:
        p = _synth_trim_log(lambda r, m: np.where((r > 3000) | (m > 75), 7.0, 0.5), d)
        cr = analyze_log(p, _opts(profile=EngineProfile(mods=["Ported heads", "Long-tube headers"])))
    af = [f for f in cr.findings if f.id == "APPLY_FUEL"][0]
    assert any("expected from your airflow mods" in c.lower() for c in af.corrections)


def test_mod_long_tubes_add_header_leak_to_bank_imbalance():
    import numpy as np, pandas as pd
    from tuneassist.core import _apply_mod_insights
    from tuneassist.diagnostics import diagnose
    from tuneassist.engine_gm import resolve_columns
    n = 600
    df = pd.DataFrame({"Engine RPM": np.full(n, 2000.0), "MAP": np.full(n, 45.0),
                       "Throttle Position": np.full(n, 25.0), "Coolant Temp": np.full(n, 195.0),
                       "Short Term Fuel Trim Bank 1": np.full(n, 8.0),
                       "Long Term Fuel Trim Bank 1": np.zeros(n),
                       "Short Term Fuel Trim Bank 2": np.full(n, -3.0),
                       "Long Term Fuel Trim Bank 2": np.zeros(n)})
    fs = diagnose(df, resolve_columns(df), Config())

    class _S:
        offset = {}
    _apply_mod_insights(fs, _S(), None, ["Long-tube headers"])
    bank = [f for f in fs if f.id == "BANK_IMBALANCE"][0]
    assert any("exhaust leak" in c.lower() for c in bank.causes)


def test_sae_closed_loop_strings_produce_grid():
    # 'Fuel System Status' as SAE 'CL - Normal' / 'OL - ...' must be understood as
    # closed loop (not the literal word 'closed') so trims build a correction grid
    import numpy as np, pandas as pd, tempfile
    from tuneassist.engine_gm import analyze, resolve_columns, Config as GCfg
    n = 2000
    rpm = np.clip(1800 + 900 * np.sin(np.arange(n) / 40), 700, 3200)
    mapk = np.clip(45 + 25 * np.sin(np.arange(n) / 33), 22, 90)
    status = np.where(np.arange(n) % 5 == 0, "OL - Accel/Decel", "CL - Normal")
    df = pd.DataFrame({"Engine RPM": rpm, "Intake Manifold Absolute Pressure": mapk,
                       "Throttle Position": np.clip(mapk - 20, 0, 80),
                       "Coolant Temp": np.full(n, 195.0), "Fuel System #1 Status": status,
                       "Short Term Fuel Trim Bank 1": np.full(n, 4.0),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    res = analyze(df, GCfg())
    assert not res.correction.empty and int(res.confidence.values.sum()) > 0


def test_findings_name_exact_vendor_tables():
    # GM: lead finding names the Main VE table; a finding names its table
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(tune_spark=True))
    af = [f for f in cr.findings if f.id == "APPLY_FUEL"][0]
    assert any("Main VE table" in c for c in af.corrections)
    assert any("In your tune:" in c for f in cr.findings for c in f.corrections)


def test_holley_finding_names_base_fuel_table():
    cr = analyze_log(os.path.join(FIX, "sniper_sample.csv"), _opts())
    af = [f for f in cr.findings if f.id == "APPLY_FUEL"][0]
    assert any("Base Fuel table" in c for c in af.corrections)


def test_hptuners_comma_in_channel_name_is_repaired():
    # a custom AEM wideband auto-named with commas used to over-split the header
    # and silently drop/misalign the wideband. The id row (no commas) is truth.
    import tempfile
    from tuneassist.engine_gm import load_log, resolve_columns
    body = (
        "HP Tuners CSV Log File\nVersion: 1.0\n\n"
        "[Channel Information]\n"
        "0,12,11,2340,5130\n"
        "Offset,Engine RPM (SAE),Intake Manifold Absolute Pressure (SAE),"
        "AEM EQ -> AEM 30-(0300,2340,5130),AEM - AFR\n"
        "s,rpm,kPa,lambda,\n"
        "[Channel Data]\n"
        "0.00,800,35,0.99,14.2\n0.05,820,36,0.98,14.1\n0.10,810,35,0.99,14.3\n")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "comma.csv")
        open(p, "w", encoding="utf-8").write(body)
        df, _ = load_log(p)
        assert df.shape[1] == 5                       # aligned to the id row, not 7
        col = resolve_columns(df)
        assert col.get("rpm") == "Engine RPM (SAE)"
        assert col.get("afr_actual") == "AEM - AFR"   # the AFR, not the EQ channel
        import pandas as pd
        assert abs(float(pd.to_numeric(df["AEM - AFR"]).iloc[0]) - 14.2) < 0.01


def test_platform_make_architecture_axes_in_contract():
    from tuneassist.core import platform_label, stoich_from_ethanol
    assert platform_label("gm") == "HP Tuners" and platform_label("holley") == "Holley EFI"
    assert 9.5 < stoich_from_ethanol(85) < 10.0     # E85 ~9.85
    d = analyze_log(os.path.join(FIX, "ride42.csv"), _opts()).to_dict()
    assert d["platform"] == "gm" and d["platform_label"] == "HP Tuners"
    assert d["make"] == "gm" and d["architecture"] == "gm_gen3_ls"


def test_legacy_garage_record_without_make_loads():
    # a garage saved before the make/architecture axes existed must still load
    from tuneassist.core import record_to_opts, opts_to_record
    plat, opts = record_to_opts({"platform": "gm", "stoich": 14.7,
                                 "airflow_mode": "maf"})
    assert plat == "gm" and opts.make == "gm" and opts.architecture == "gm_gen3_ls"
    rec = opts_to_record(plat, opts)
    assert rec["make"] == "gm" and rec["architecture"] == "gm_gen3_ls"


def test_ethanol_channel_sets_stoich_and_flags_flex_fuel():
    import numpy as np, pandas as pd, tempfile
    n = 1500
    rpm = np.clip(1500 + 1400 * np.sin(np.arange(n) / 35), 700, 4200)
    mapk = np.clip(45 + 30 * np.sin(np.arange(n) / 28), 22, 95)
    df = pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": rpm,
                       "Intake Manifold Absolute Pressure": mapk,
                       "Throttle Position": np.clip(mapk - 20, 0, 80),
                       "Coolant Temp": np.full(n, 195.0),
                       "Commanded AFR": np.full(n, 14.7),
                       "Ethanol Fuel %": np.full(n, 85.0),
                       "Short Term Fuel Trim Bank 1": np.full(n, 3.0),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "e85.csv")
        df.to_csv(p, index=False)
        cr = analyze_log(p, _opts())
    assert any(f.id == "FUEL_ETHANOL" for f in cr.findings)
    assert any("ethanol" in n.lower() for n in cr.notes)
    assert cr.summary is not None  # ran with the E85 stoich, no crash


def test_duplicate_channel_names_are_deduped():
    # real logs sometimes log the same PID several times -> pandas rejects dup
    # names; the loader must suffix repeats instead of crashing.
    import tempfile
    from tuneassist.engine_gm import load_log
    body = ("HP Tuners CSV Log File\nVersion: 1.0\n\n[Channel Information]\n"
            "0,12,11,2120,2120\n"
            "Offset,Engine RPM,Intake Manifold Absolute Pressure (SAE),"
            "Engine Oil Pressure,Engine Oil Pressure\n"
            "s,rpm,kPa,psi,psi\n[Channel Data]\n"
            "0.00,800,35,40,40\n0.05,820,36,41,41\n")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dup.csv")
        open(p, "w", encoding="utf-8").write(body)
        df, _ = load_log(p)
        assert df.shape[1] == 5
        assert len(set(df.columns)) == 5            # all unique after dedup
        assert "Engine RPM" in df.columns


def test_gen4_detected_and_prescribes_maf_only():
    # a VVT cam channel is the Gen-4 fingerprint -> architecture gm_gen4_ls, and
    # the lead change must target the MAF curve, not a VE table.
    import numpy as np, pandas as pd, tempfile
    n = 1500
    rpm = np.clip(1500 + 1400 * np.sin(np.arange(n) / 35), 700, 4200)
    mapk = np.clip(45 + 30 * np.sin(np.arange(n) / 28), 22, 95)
    df = pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": rpm,
                       "Intake Manifold Absolute Pressure (SAE)": mapk,
                       "Throttle Position": np.clip(mapk - 20, 0, 80),
                       "Coolant Temp": np.full(n, 195.0), "Absolute Load": mapk,
                       "Intake Cam Angle": np.full(n, 5.0),   # VVT -> Gen 4
                       "Commanded AFR": np.full(n, 14.7),
                       "Short Term Fuel Trim Bank 1": np.full(n, 7.0),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "g4.csv")
        df.to_csv(p, index=False)
        cr = analyze_log(p, _opts())
    assert cr.make == "gm" and cr.architecture == "gm_gen4_ls"   # not Ford!
    af = [f for f in cr.findings if f.id == "APPLY_FUEL"][0]
    joined = " ".join(af.corrections)
    assert "MAF" in joined and "Main VE table" not in joined


def test_torque_based_ecm_detected_as_gen4():
    # a torque-request channel + dynamic airflow = Gen 4 (Gen 3 isn't torque-based)
    import numpy as np, pandas as pd, tempfile
    n = 1200
    rpm = np.clip(1500 + 1200 * np.sin(np.arange(n) / 30), 700, 4000)
    mapk = np.clip(45 + 30 * np.sin(np.arange(n) / 25), 22, 95)
    df = pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": rpm,
                       "Intake Manifold Absolute Pressure (SAE)": mapk,
                       "Throttle Position": np.clip(mapk - 20, 0, 80),
                       "Coolant Temp": np.full(n, 195.0), "Dynamic Airflow": mapk,
                       "TCS Desired Engine Torque": np.full(n, 200.0),
                       "Short Term Fuel Trim Bank 1": np.full(n, 4.0),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tq.csv")
        df.to_csv(p, index=False)
        cr = analyze_log(p, _opts())
    assert cr.make == "gm" and cr.architecture == "gm_gen4_ls"


def test_no_map_log_explains_missing_load_axis():
    # Ford/OBD-II style: RPM + trims but no manifold MAP -> can't build the grid,
    # but the trim diagnosis still applies and we say why + name the Ford case.
    import numpy as np, pandas as pd, tempfile
    n = 1500
    rpm = np.clip(1500 + 1400 * np.sin(np.arange(n) / 35), 700, 4200)
    df = pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": rpm,
                       "Throttle Position": np.clip((rpm - 700) / 3500 * 60, 0, 60),
                       "Coolant Temp": np.full(n, 195.0),
                       "Short Term Fuel Trim Bank 1": np.full(n, -8.0),
                       "Long Term Fuel Trim Bank 1": np.zeros(n)})
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "nomap.csv")
        df.to_csv(p, index=False)
        cr = analyze_log(p, _opts())
    assert not cr.has_grid
    assert cr.empty_reason and "MAP" in cr.empty_reason
    blob = " ".join(cr.prescription.actions + [cr.prescription.drive]).lower()
    assert "map" in blob and "ford" in blob
    assert any(f.id in ("RICH_CRUISE", "LEAN_CRUISE") for f in cr.findings)


def test_no_rpm_channel_with_data_points_at_rpm():
    # a log with rows but no RPM channel -> tell the user to add Engine RPM
    import numpy as np, pandas as pd
    from tuneassist.triage import triage
    n = 200
    df = pd.DataFrame({"Time": np.arange(n) * 0.05,
                       "Injector Pulse Width Avg. Bank 1": np.full(n, 4.0)})
    r = triage(df, {"time": "Time"})
    assert r.state == "NO_DATA" and r.can_correct is False
    blob = (r.detail + " " + " ".join(r.recommendations)).lower()
    assert "engine rpm" in blob


def test_correction_tsv_is_paste_ready():
    # the grid copies as tab-separated percent values, RPM rows x MAP cols, with
    # low-confidence cells -> 0 (so a multiply-by-percent leaves them unchanged)
    from tuneassist.core import correction_tsv, grid_tsv, series_tsv
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts())
    tsv = correction_tsv(cr)
    assert tsv and "\t" in tsv and "\n" in tsv
    rows = tsv.split("\n")
    ncols = cr.result.correction.shape[1]
    assert all(len(r.split("\t")) == ncols for r in rows)   # rectangular, header-free
    assert rows[0].split("\t")[0] in ("0",) or rows[0].split("\t")[0].lstrip("-").replace(".", "").isdigit()
    # a known multiplier converts to percent
    assert grid_tsv(None) is None
    import pandas as pd
    s = pd.Series([1.05, 1.00, float("nan")])
    assert series_tsv(s, "percent") == "5\t0\t0"


def test_spark_tsv_is_raw_degrees():
    from tuneassist.core import spark_tsv
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(tune_spark=True))
    tsv = spark_tsv(cr)
    if tsv:                                          # ride42 has a knock channel
        assert "\t" in tsv                           # degrees, not percent


def test_cold_log_reports_blocker_not_grid():
    d = analyze_log(os.path.join(FIX, "jr42.csv"), _opts()).to_dict()
    assert d.get("empty_reason") and "operating temp" in d["empty_reason"]
    assert "correction" not in d or not d["correction"]["cells"]


def test_no_rpm_log_gates():
    cr = analyze_log(os.path.join(FIX, "protuner12.csv"), _opts())
    assert cr.stage == "GET_RUNNING"
    assert cr.triage.can_correct is False


def test_maf_mode_emits_frequency_table_when_available():
    # ride42 has no MAF frequency channel -> maf section absent, no crash
    d = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(airflow_mode="maf")).to_dict()
    assert "maf" not in d or d["maf"]["axis"] == "frequency_hz"


def test_headless_writes_nothing_when_out_dir_none(tmp_path=None):
    # out_dir=None means no CSV side-effects -- safe for pure JSON/UI use
    before = set(os.listdir(FIX))
    analyze_log(os.path.join(FIX, "ride42.csv"), _opts(), out_dir=None)
    assert set(os.listdir(FIX)) == before


def test_danger_bands_lean_needs_load_and_rich_is_flagged():
    from tuneassist.core import _danger_bands
    t = [round(i * 0.1, 1) for i in range(40)]
    # idle/decel lean spike (no load) must NOT flag; WOT lean must flag
    afr, tps, mp, cmd = [], [], [], []
    for i in range(40):
        if 10 <= i < 20:           # WOT and lean -> dangerous
            afr.append(13.8); tps.append(90); mp.append(98); cmd.append(12.8)
        elif 25 <= i < 35:         # way rich, light throttle
            afr.append(10.5); tps.append(30); mp.append(60); cmd.append(13.0)
        else:                      # closed-throttle decel: lean but harmless
            afr.append(16.5); tps.append(0); mp.append(25); cmd.append(14.7)
    tr = {"afr_actual": afr, "afr_cmd": cmd, "tps": tps, "map": mp}
    bands = _danger_bands(t, tr, 14.7)
    types = {b["type"] for b in bands}
    assert "lean" in types and "rich" in types
    lean = [b for b in bands if b["type"] == "lean"][0]
    assert lean["from"] >= 1.0 and lean["to"] <= 2.0      # only the WOT window
    # the closed-throttle lean stretch (very lean AFR, zero TPS) produced no band
    assert not any(b["type"] == "lean" and b["from"] >= 3.5 for b in bands)


def test_danger_bands_empty_without_measured_afr():
    from tuneassist.core import _danger_bands
    t = [round(i * 0.1, 1) for i in range(20)]
    tr = {"afr_cmd": [12.8] * 20, "tps": [90] * 20, "map": [98] * 20}  # no afr_actual
    assert _danger_bands(t, tr, 14.7) == []


# --- custom VE table axes (real user request: grid must match his VE table) ---
RPM_BP = [400, 800, 1200, 1600, 2000, 2400, 2800, 3200, 3600, 4000,
          4400, 4800, 5200, 5600, 6000, 6400, 6800, 7200, 7600, 8000]   # 20
MAP_BP = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85,
          90, 95, 100, 105]                                             # 19


def test_parse_and_clean_axes():
    from tuneassist.core import parse_axis, clean_ve_axes
    assert parse_axis("400, 800,1200\t1600 2000") == [400, 800, 1200, 1600, 2000]
    assert parse_axis([20, 20, 40, 30]) == [20, 40, 30]          # order kept, deduped
    assert parse_axis([105, 103, 101, 20]) == [105, 103, 101, 20]  # descending kept
    assert clean_ve_axes({"rpm": "400 800 1200", "map": "20,40,60"}) == \
        {"rpm": [400, 800, 1200], "map": [20, 40, 60]}
    assert clean_ve_axes({"rpm": "400", "map": "20,40"}) is None  # need >=2 per axis
    assert clean_ve_axes(None) is None


def test_parse_ve_table_copy_with_axis():
    # VCM Editor "Copy with Axis" pastes the whole table: RPM header row (led by
    # '%', trailed by 'rpm'), one data row per MAP value, a trailing 'kPa'.
    from tuneassist.core import parse_ve_table, clean_ve_axes
    table = ("%\t400\t800\t1200\t1600\trpm\n"
             "15\t38.2\t42.5\t45.5\t46.1\n"
             "20\t41.5\t45.7\t49.3\t49.8\n"
             "105\t79.2\t75.0\t74.1\t74.7\n"
             "kPa")
    p = parse_ve_table(table)
    assert p["rpm"] == [400, 800, 1200, 1600]      # cell values + %/rpm ignored
    assert p["map"] == [15, 20, 105]               # leading value of each data row
    # clean_ve_axes accepts the raw paste under {"table": ...}
    assert clean_ve_axes({"table": table}) == {"rpm": [400, 800, 1200, 1600],
                                               "map": [15, 20, 105]}
    # a plain (no-axis) paste / junk returns None so we fall back to manual entry
    assert parse_ve_table("38.2\t42.5\n41.5\t45.7") is None


def _synthetic_gm_log(path):
    import numpy as np, pandas as pd
    n = 4000
    rpm = np.clip(3800 + 3600 * np.sin(np.arange(n) / 37), 600, 8000)
    mapk = np.clip(60 + 46 * np.sin(np.arange(n) / 23), 15, 105)
    knock = np.where(np.arange(n) % 400 == 0, 2.0, 0.0)         # occasional retard
    pd.DataFrame({"Time": np.arange(n) * 0.05, "Engine RPM": rpm,
                  "Intake Manifold Absolute Pressure": mapk,
                  "Throttle Position": np.clip(mapk - 15, 0, 95),
                  "Coolant Temp": np.full(n, 195.0),
                  "Commanded AFR": np.full(n, 14.2),
                  "Spark Advance": np.clip(28 - (mapk - 40) * 0.2, 8, 34),
                  "Knock Retard": knock,
                  "Short Term Fuel Trim Bank 1": np.full(n, 6.0),
                  "Long Term Fuel Trim Bank 1": np.zeros(n)}).to_csv(path, index=False)


def test_custom_ve_axes_resamples_to_table_and_transposes_tsv():
    import tempfile
    from tuneassist import core
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "log.csv")
        _synthetic_gm_log(p)
        # tune_spark on: spark has its OWN table axes, so custom VE axes must not
        # leak into it (regression: inf bin edges crashed _interval_label).
        cr = analyze_log(p, _opts(tune_spark=True,
                                  ve_axes={"rpm": RPM_BP, "map": MAP_BP}), out_dir=None)
    dd_spark = cr.to_dict()                                # must not raise
    if dd_spark.get("spark", {}).get("cells"):
        assert any("-" in c["rpm"] for c in dd_spark["spark"]["cells"])  # default bins
    corr = cr.result.correction
    # grid is now indexed by the EXACT breakpoints (RPM rows, MAP cols)
    assert corr.index.tolist() == [float(x) for x in RPM_BP]
    assert corr.columns.tolist() == [float(x) for x in MAP_BP]
    assert corr.shape == (20, 19)
    dd = cr.to_dict()
    assert dd["ve_axes"] == {"rpm": [float(x) for x in RPM_BP],
                             "map": [float(x) for x in MAP_BP]}
    # the paste TSV is transposed to VCM Editor layout: MAP rows x RPM cols
    rows = core.correction_tsv(cr).split("\n")
    assert len(rows) == 19                       # one row per MAP breakpoint
    assert all(len(r.split("\t")) == 20 for r in rows)   # one col per RPM breakpoint


def test_holley_descending_nonuniform_map_axis_is_preserved():
    # Holley Sniper lists MAP descending + non-uniform (210,158,105,103,...,20).
    # The grid/TSV must mirror that exact order so a paste isn't flipped.
    import tempfile
    from tuneassist import core
    hol_map = [105, 103, 101, 99, 97, 95, 90, 85, 80, 75, 70, 60, 50, 40, 30, 20]
    rpm = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 5000, 6000, 7000, 8000]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "log.csv")
        _synthetic_gm_log(p)
        cr = analyze_log(p, _opts(ve_axes={"rpm": rpm, "map": hol_map}), out_dir=None)
    # columns preserve the descending, non-uniform paste order exactly
    assert cr.result.correction.columns.tolist() == [float(x) for x in hol_map]
    assert cr.result.correction.index.tolist() == [float(x) for x in rpm]
    # TSV is MAP-rows x RPM-cols; first row is the FIRST pasted MAP (105), last is 20
    rows = core.correction_tsv(cr).split("\n")
    assert len(rows) == len(hol_map) and len(rows[0].split("\t")) == len(rpm)


def test_no_axes_keeps_default_interval_bins():
    cr = analyze_log(os.path.join(FIX, "ride42.csv"), _opts(), out_dir=None)
    assert cr.ve_axes is None
    # default grid still labels cells as ranges like "400-800", not breakpoints
    cells = cr.to_dict().get("correction", {}).get("cells", [])
    assert any("-" in c["rpm"] for c in cells)


def test_ve_axes_round_trip_through_garage():
    from tuneassist.core import opts_to_record, record_to_opts
    opts = _opts(ve_axes={"rpm": RPM_BP, "map": MAP_BP})
    rec = opts_to_record("gm", opts)
    assert rec["ve_axes"]["rpm"][0] == 400 and len(rec["ve_axes"]["map"]) == 19
    _, opts2 = record_to_opts(rec)
    assert opts2.ve_axes == {"rpm": [float(x) for x in RPM_BP],
                             "map": [float(x) for x in MAP_BP]}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all core tests passed")
