"""Tests for submit.py -- bundle building + privacy (offline; never sends)."""
import sys, os, json, zipfile, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist import submit, __version__
from tuneassist.core import analyze_log, SessionOpts
from tuneassist.engine_gm import Config
from tuneassist.profile import EngineProfile

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
RIDE = os.path.join(FIX, "ride42.csv")


def test_disabled_by_default():
    # no SUBMIT_URL configured -> the whole feature is dormant
    assert submit.SUBMIT_URL == ""
    assert submit.is_enabled() is False


def test_metadata_is_non_identifying():
    opts = SessionOpts(cfg=Config(), airflow_mode="ve_sd",
                       profile=EngineProfile(block="iron", displacement=5.3,
                                             mods=["Ported heads"]))
    cr = analyze_log(RIDE, opts)
    meta = submit.build_metadata(cr, opts, note="runs lean up top", contact="me@x.com")
    assert meta["tuneassist_version"] == __version__
    assert meta["platform"] == "gm"
    assert meta["stage"] == "TUNE_VE_SD"
    assert meta["profile"]["block"] == "iron"
    assert "Ported heads" in meta["profile"]["mods"]
    assert meta["note"] == "runs lean up top"
    assert meta["contact"] == "me@x.com"
    # must NOT carry the garage name / nickname
    blob = json.dumps(meta).lower()
    assert "nickname" not in blob and "vehicle" not in blob


def test_build_bundle_contains_log_and_metadata():
    opts = SessionOpts(cfg=Config())
    cr = analyze_log(RIDE, opts)
    # redirect submissions dir into a temp folder so we don't touch real home
    with tempfile.TemporaryDirectory() as d:
        submit._submissions_dir = lambda: d
        bundle = submit.build_bundle(RIDE, cr, opts, note="hi")
        assert bundle.endswith(".zip") and os.path.exists(bundle)
        with zipfile.ZipFile(bundle) as z:
            names = z.namelist()
            assert "submission.json" in names
            assert any(n.startswith("log/") and n.endswith("ride42.csv") for n in names)
            meta = json.loads(z.read("submission.json"))
            assert meta["note"] == "hi"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all submit tests passed")
