"""Tests for garage.py (on-disk per-vehicle store) and the opts<->record codec."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist import garage, cams
from tuneassist.profile import EngineProfile
from tuneassist.engine_gm import Config
from tuneassist.wizard import SessionOpts, _opts_to_record, _record_to_opts


def test_load_missing_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        data = garage.load(os.path.join(d, "nope.json"))
        assert data["vehicles"] == {}


def test_save_then_load_roundtrips():
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "garage.json")
        data = garage.load(gp)
        garage.upsert(data, "truck", {"platform": "gm", "updated": "2026-01-01"})
        garage.save(data, gp)
        again = garage.load(gp)
        assert "truck" in garage.list_vehicles(again)
        assert garage.get(again, "truck")["platform"] == "gm"


def test_corrupt_file_is_tolerated():
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "garage.json")
        with open(gp, "w") as fh:
            fh.write("{ this is not json ")
        data = garage.load(gp)            # must not raise
        assert data["vehicles"] == {}


def test_list_orders_by_updated_desc():
    data = {"version": 1, "vehicles": {
        "a": {"updated": "2026-01-01"}, "b": {"updated": "2026-06-01"}}}
    assert garage.list_vehicles(data) == ["b", "a"]


def test_delete():
    data = {"version": 1, "vehicles": {"a": {}}}
    assert garage.delete(data, "a") is True
    assert garage.delete(data, "a") is False


def test_opts_record_roundtrip_preserves_everything():
    cfg = Config(); cfg.stoich = 9.76
    opts = SessionOpts(cfg=cfg, airflow_mode="maf", tune_spark=True, find_power=True,
                       cam_spec=cams.CamSpec(intake_dur_050=224, exhaust_dur_050=224, lsa=112),
                       profile=EngineProfile(block="alum", compression=10.5,
                                             displacement=5.7, power_adder="boost",
                                             engine="Chevy LS1 5.7 (aluminum)",
                                             mods=["Ported heads", "Turbo"]))
    opts.cam_points = cams.starting_points(opts.cam_spec)

    rec = _opts_to_record("gm", opts)
    platform, back = _record_to_opts(rec)

    assert platform == "gm"
    assert back.cfg.stoich == 9.76
    assert back.airflow_mode == "maf"
    assert back.tune_spark and back.find_power
    assert back.cam_spec.intake_dur_050 == 224
    assert back.cam_points.klass == opts.cam_points.klass
    assert back.profile.block == "alum" and back.profile.compression == 10.5
    assert back.profile.engine == "Chevy LS1 5.7 (aluminum)"
    assert back.profile.mods == ["Ported heads", "Turbo"]


def test_record_to_opts_handles_minimal_record():
    # an old/sparse record (no cam, no profile) must still rebuild cleanly
    platform, opts = _record_to_opts({"platform": "holley", "stoich": 14.7})
    assert platform == "holley" and opts.cam_points is None and opts.profile is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all garage tests passed")
