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
