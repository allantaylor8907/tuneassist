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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all core tests passed")
