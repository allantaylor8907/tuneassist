"""Tests for update.py -- all offline (no real network calls)."""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tuneassist
from tuneassist import update


def test_package_exposes_version():
    assert isinstance(tuneassist.__version__, str) and tuneassist.__version__


def test_version_compare():
    assert update.is_newer("v0.2.0", "0.1.0")
    assert update.is_newer("1.0.1", "1.0.0")
    assert not update.is_newer("0.1.0", "0.1.0")
    assert not update.is_newer("v1.0", "v1.0.1")
    # odd tags degrade instead of raising
    assert update._parse("weird") == (0,)
    assert update._parse("v2.3.4-rc1") == (2, 3, 4)


def test_pick_asset_matches_this_os(monkeypatch=None):
    assets = [
        {"name": "tuneassist-linux-x64", "browser_download_url": "http://x/linux"},
        {"name": "tuneassist-macos-arm64", "browser_download_url": "http://x/mac"},
        {"name": "tuneassist-windows-x64.exe", "browser_download_url": "http://x/win"},
    ]
    for key, suffix, expect in (("linux", "", "http://x/linux"),
                                ("macos", "", "http://x/mac"),
                                ("windows", ".exe", "http://x/win")):
        update._os_asset_key = (lambda k=key, s=suffix: (lambda: (k, s)))()
        url, name = update._pick_asset(assets)
        assert url == expect


def test_update_ps_script_handles_paths_with_spaces_and_parens():
    # the failing real-world case: 'tuneassist-windows-x64 (2).exe'
    exe = r"C:\Users\Allan\Downloads\tuneassist-windows-x64 (2).exe"
    new = exe + ".new"
    s = update._update_ps_script(exe, new, pid=4321)
    assert "Wait-Process -Id 4321" in s              # waits on the real process
    # LiteralPath single-quoted (parens/spaces safe), moving .new ONTO the .exe
    assert f"-LiteralPath '{new}' -Destination '{exe}' -Force" in s
    assert f"Start-Process -FilePath '{exe}'" in s   # relaunch after swap
    assert "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path" in s  # self-deletes


def test_self_update_refuses_when_not_frozen():
    # running from source -> point at the package manager, don't touch anything
    assert not update.is_frozen()
    ok, msg = update.self_update(update.UpdateInfo("0.1.0", "9.9.9", "url", "a", "n"))
    assert ok is False and ("pipx" in msg or "pip install" in msg)


def test_passive_check_disabled_by_env():
    os.environ["TUNEASSIST_NO_UPDATE_CHECK"] = "1"
    try:
        assert update.passive_check() is None
    finally:
        del os.environ["TUNEASSIST_NO_UPDATE_CHECK"]


def test_passive_check_throttles_and_caches(monkeypatch=None):
    # this test exercises the THROTTLE logic, so neutralize the env guards (the
    # CI/disable guards are covered by test_passive_check_disabled_by_env). CI
    # runners set CI=1, which would otherwise short-circuit the unforced call.
    saved = {k: os.environ.pop(k, None)
             for k in ("CI", "TUNEASSIST_NO_UPDATE_CHECK")}
    try:
        with tempfile.TemporaryDirectory() as d:
            statefile = os.path.join(d, "update.json")
            update._state_path = lambda: statefile
            calls = {"n": 0}

            def fake_latest():
                calls["n"] += 1
                return update.UpdateInfo(tuneassist.__version__, "99.0.0",
                                         "page", None, None)
            update.check_for_update = fake_latest

            first = update.passive_check(force=True)          # due -> hits "network"
            assert first and first.latest == "99.0.0" and calls["n"] == 1
            # not forced + just checked -> must NOT hit network again, but still
            # surfaces the cached pending update
            second = update.passive_check(force=False)
            assert calls["n"] == 1
            assert second and update.is_newer(second.latest, tuneassist.__version__)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all update tests passed")
