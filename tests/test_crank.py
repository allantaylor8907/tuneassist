"""Tests for crank.py -- the crank/no-start diagnosis."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np, pandas as pd
from tuneassist.core import analyze_log, SessionOpts
from tuneassist.engine_gm import Config


def _opts():
    return SessionOpts(cfg=Config())


def _crank_log(path, **chans):
    """A log that spins in the crank band but never catches -> CRANKING_NO_START."""
    n = 400
    rpm = np.clip(210 + 25 * np.sin(np.arange(n) / 7), 120, 320)   # cranking, never > 500
    data = {"Time": np.arange(n) * 0.05, "Engine RPM": rpm}
    data.update(chans)
    pd.DataFrame(data).to_csv(path, index=False)


def _ids(cr):
    return {f.id for f in cr.findings}


def test_no_injection_and_sync_flagged():
    n = 400
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.csv")
        # injectors not pulsing, no spark channel logged
        _crank_log(p, **{"Injector Pulse Width": np.zeros(n)})
        cr = analyze_log(p, _opts(), out_dir=None)
    assert cr.triage.state == "CRANKING_NO_START"
    ids = _ids(cr)
    assert "NOSTART_NO_INJECTION" in ids        # injector pulse ~0
    assert "NOSTART_SYNC_SUSPECT" in ids        # no timing logged -> can't confirm sync
    # the no-injection finding is critical and leads
    assert cr.findings[0].severity == "critical"


def test_getting_fuel_and_spark_but_flooded():
    n = 400
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.csv")
        _crank_log(p, **{"Injector Pulse Width": np.full(n, 4.0),
                         "Fuel Pressure": np.full(n, 52.0),
                         "Ignition Timing": np.full(n, 6.0),
                         "Wideband AFR": np.full(n, 8.2)})
        cr = analyze_log(p, _opts(), out_dir=None)
    ids = _ids(cr)
    assert "NOSTART_INJECTION_OK" in ids
    assert "NOSTART_FUEL_PRESSURE_OK" in ids
    assert "NOSTART_SPARK_LOGGED" in ids
    assert "NOSTART_FLOODED" in ids
    assert "NOSTART_NO_INJECTION" not in ids


def test_low_fuel_pressure_flagged():
    n = 400
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.csv")
        _crank_log(p, **{"Injector Pulse Width": np.full(n, 3.5),
                         "Fuel Pressure": np.full(n, 6.0),
                         "Ignition Timing": np.full(n, 5.0)})
        cr = analyze_log(p, _opts(), out_dir=None)
    assert "NOSTART_LOW_FUEL_PRESSURE" in _ids(cr)


def test_bare_crank_log_says_log_more():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.csv")
        _crank_log(p)                            # only RPM -- nothing to diagnose from
        cr = analyze_log(p, _opts(), out_dir=None)
    ids = _ids(cr)
    assert "NOSTART_LOG_MORE" in ids and "NOSTART_SYNC_SUSPECT" in ids


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all crank tests passed")
