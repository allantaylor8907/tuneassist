"""Smoke tests: the guided session runs end-to-end on the real fixtures with
scripted IO, and lands on the expected journey stage for each. Also a multi-pass
test that two successive logs walk forward without crashing."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist.wizard import (run_session, WizardIO, _ingest, detect_platform,
                               _run_one, SessionOpts)
from tuneassist.engine_gm import Config
from tuneassist import garage

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _stage_for(fixture, airflow_mode="ve_sd", tune_spark=False):
    """Run one pass through the wizard internals and return the stage it picked."""
    path = os.path.join(FIX, fixture)
    platform = detect_platform(path)
    opts = SessionOpts(cfg=Config(), airflow_mode=airflow_mode, tune_spark=tune_spark)
    with tempfile.TemporaryDirectory() as out:
        return _run_one(WizardIO(scripted=[]), path, platform, opts, out,
                        history=[], pass_no=1)


def test_ride42_ve_mode_lands_on_tune_ve_sd():
    assert _stage_for("ride42.csv", airflow_mode="ve_sd") == "TUNE_VE_SD"


def test_ride42_maf_mode_lands_on_tune_maf():
    assert _stage_for("ride42.csv", airflow_mode="maf") == "TUNE_MAF"


def test_protuner12_gates_at_get_running():
    assert _stage_for("protuner12.csv") == "GET_RUNNING"


def test_jr42_cold_has_no_confident_cells():
    assert _stage_for("jr42.csv") in ("DIAL_IDLE_CRUISE", "TUNE_VE_SD")


def test_spark_enabled_runs_without_crash():
    # ride42 has no knock channel -> spark refuses gracefully, no crash
    assert _stage_for("ride42.csv", tune_spark=True) in (
        "TUNE_VE_SD", "TUNE_MAF", "TUNE_POWER", "CONVERGED")


def test_full_session_quits_cleanly_on_q():
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "garage.json")
        # blank vehicle name (don't save), then 'q' at the log prompt
        run_session(initial_log=None, io=WizardIO(scripted=["", "q"]), garage_path=gp)


def test_two_pass_session_remembers_setup():
    ride = os.path.join(FIX, "ride42.csv")
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "garage.json")
        # name; nickname(blank); gm; fuel1; airflow1; spark=no; cam=no;
        # analyze-new=yes; <ride>; keep-setup=yes (no re-quiz); stop=no
        io = WizardIO(scripted=["5.3truck", "", "gm", "1", "1", False, False,
                                True, ride, True, False])
        run_session(initial_log=ride, io=io, out_dir=os.path.join(d, "out"),
                    garage_path=gp)
        assert io._i >= 10


def test_garage_persists_vehicle_across_runs():
    ride = os.path.join(FIX, "ride42.csv")
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "garage.json")
        out = os.path.join(d, "out")
        # run 1: create "5.7" with nickname "Red", then stop
        io1 = WizardIO(scripted=["5.7", "Red", "gm", "1", "1", False, False, False])
        run_session(initial_log=ride, io=io1, out_dir=out, garage_path=gp)

        # the vehicle must be on disk now, with its setup, nickname + a history entry
        data = garage.load(gp)
        assert "5.7" in garage.list_vehicles(data)
        rec = garage.get(data, "5.7")
        assert rec["platform"] == "gm" and rec.get("stage")
        assert rec.get("nickname") == "Red"
        assert len(rec.get("history", [])) >= 1

        # run 2: reload by NICKNAME, keep setup, stop (proves nickname selection works)
        io2 = WizardIO(scripted=["Red", True, False])
        run_session(initial_log=ride, io=io2, out_dir=out, garage_path=gp)
        # nickname must survive the re-save in run 2
        assert garage.get(garage.load(gp), "5.7").get("nickname") == "Red"


def _seed_two_vehicles(gp, out):
    ride = os.path.join(FIX, "ride42.csv")
    run_session(initial_log=ride, garage_path=gp, out_dir=out,
                io=WizardIO(scripted=["Goldie5.3", "Goldie", "gm", "1", "1", False, False, False]))
    run_session(initial_log=ride, garage_path=gp, out_dir=out,
                io=WizardIO(scripted=["n", "Bandit5.7", "Bandit", "gm", "1", "1", False, False, False]))


def test_garage_rename_from_picker():
    ride = os.path.join(FIX, "ride42.csv")
    with tempfile.TemporaryDirectory() as d:
        gp, out = os.path.join(d, "garage.json"), os.path.join(d, "out")
        _seed_two_vehicles(gp, out)
        # rename target "Goldie5.3" -> nickname "Sunshine", then select it, keep, stop
        io = WizardIO(scripted=["r Goldie5.3", "Sunshine", "Goldie5.3", True, False])
        run_session(initial_log=ride, garage_path=gp, out_dir=out, io=io)
        assert garage.get(garage.load(gp), "Goldie5.3")["nickname"] == "Sunshine"


def test_garage_delete_from_picker():
    ride = os.path.join(FIX, "ride42.csv")
    with tempfile.TemporaryDirectory() as d:
        gp, out = os.path.join(d, "garage.json"), os.path.join(d, "out")
        _seed_two_vehicles(gp, out)
        # delete "Bandit5.7" (confirm yes), then select remaining #1, keep, stop
        io = WizardIO(scripted=["d Bandit5.7", True, "1", True, False])
        run_session(initial_log=ride, garage_path=gp, out_dir=out, io=io)
        data = garage.load(gp)
        assert "Bandit5.7" not in garage.list_vehicles(data)
        assert "Goldie5.3" in garage.list_vehicles(data)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all wizard tests passed")
