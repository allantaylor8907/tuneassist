"""Triage tests: each builds a minimal synthetic log for one vehicle state."""
import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tuneassist.triage import triage, TriageThresholds

COL = {"rpm": "rpm", "time": "t", "tps": "tps", "map": "map"}


def _log(rpm, tps=None, mapk=None, hz=20):
    n = len(rpm)
    t = np.arange(n) / hz
    return pd.DataFrame({
        "t": t, "rpm": rpm,
        "tps": tps if tps is not None else np.zeros(n),
        "map": mapk if mapk is not None else np.full(n, 35.0),
    })


def test_no_crank():
    df = _log(np.zeros(200))
    assert triage(df, COL).state == "NO_CRANK"
    assert triage(df, COL).can_correct is False


def test_cranking_no_start():
    df = _log(np.full(200, 220.0) + np.random.default_rng(1).normal(0, 15, 200))
    assert triage(df, COL).state == "CRANKING_NO_START"


def test_started_stalled():
    # cranks, catches to ~1200 for ~3s, then dies to 0
    rpm = np.concatenate([np.full(40, 220.0), np.full(80, 1200.0), np.zeros(80)])
    assert triage(_log(rpm), COL).state == "STARTED_STALLED"


def test_unstable_idle():
    rng = np.random.default_rng(2)
    rpm = 850 + rng.normal(0, 400, 400)          # wild hunt
    rpm = np.clip(rpm, 600, 1400)
    assert triage(_log(rpm), COL).state == "UNSTABLE_IDLE"


def test_idle_only():
    rng = np.random.default_rng(3)
    rpm = 800 + rng.normal(0, 40, 400)            # steady idle, no driving
    assert triage(_log(rpm), COL).state == "IDLE_ONLY"


def test_running_drive():
    rng = np.random.default_rng(4)
    n = 1000
    rpm = np.clip(1800 + 1200 * np.sin(np.arange(n) / 40) + rng.normal(0, 80, n), 700, 4500)
    mapk = np.clip(50 + 40 * np.sin(np.arange(n) / 30), 20, 98)
    tps = np.clip((mapk - 20) / 78 * 100, 0, 100)
    r = triage(_log(rpm, tps, mapk), COL)
    assert r.state == "RUNNING_DRIVE" and r.can_correct is True


def test_no_data():
    assert triage(_log(np.zeros(5)), COL).state == "NO_DATA"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all triage tests passed")
