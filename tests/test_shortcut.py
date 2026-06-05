"""Tests for shortcut.py -- exercise the file writers + install into a temp dir
(no real desktop touched). The Windows .lnk path needs COM, so it's only
smoke-checked for a sane return on Windows."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist import shortcut


def test_target_and_args_is_sane():
    target, args, is_exe = shortcut._target_and_args()
    assert target and "--tui" in args
    assert isinstance(is_exe, bool)


def test_linux_desktop_file_contents():
    with tempfile.TemporaryDirectory() as d:
        p = shortcut._write_linux_desktop(os.path.join(d, "tuneassist.desktop"),
                                          "/opt/tuneassist", ["--tui"])
        body = open(p, encoding="utf-8").read()
        assert "[Desktop Entry]" in body
        assert "Exec=/opt/tuneassist --tui" in body
        assert "Terminal=true" in body
        assert os.access(p, os.X_OK)


def test_macos_command_file_contents():
    with tempfile.TemporaryDirectory() as d:
        p = shortcut._write_macos_command(os.path.join(d, "tuneassist.command"),
                                          "/Applications/tuneassist", ["--tui"])
        body = open(p, encoding="utf-8").read()
        assert body.startswith("#!/bin/bash")
        assert "/Applications/tuneassist --tui" in body
        assert os.access(p, os.X_OK)


def test_paths_with_spaces_are_quoted():
    with tempfile.TemporaryDirectory() as d:
        p = shortcut._write_linux_desktop(os.path.join(d, "s.desktop"),
                                          "/home/me/My Tools/tuneassist", ["--tui"])
        body = open(p, encoding="utf-8").read()
        assert 'Exec="/home/me/My Tools/tuneassist" --tui' in body


def test_install_shortcut_into_tempdir():
    # don't litter the real desktop: install into a temp "desktop"
    with tempfile.TemporaryDirectory() as d:
        ok, msg = shortcut.install_shortcut(dest_dir=d)
        if sys.platform.startswith("win"):
            # COM may be unavailable in CI; just assert it returns a message
            assert isinstance(msg, str) and msg
        else:
            assert ok, msg
            made = os.listdir(d)
            assert any(f.startswith("tuneassist.") for f in made)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all shortcut tests passed")
