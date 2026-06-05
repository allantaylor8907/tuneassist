"""
shortcut.py -- create a double-clickable desktop launcher for the TUI.

`tuneassist --install-shortcut` drops an OS-native launcher on the desktop (and,
on Linux, in the app menu) that opens the full Textual UI in a terminal window.
Works whether you're running the packaged binary (`sys.frozen`) or a pip/pipx
install. Stdlib only; fails with a clear message rather than a traceback.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

APP_NAME = "tuneassist"


def _target_and_args() -> tuple[str, list[str], bool]:
    """(launch_target, args, target_is_executable).

    Frozen binary -> run the exe directly with --tui. Source/pip install -> the
    `tuneassist` console script if it's on PATH, else `python -m tuneassist.cli`.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ["--tui"], True
    script = shutil.which(APP_NAME)
    if script:
        return script, ["--tui"], True
    return sys.executable, ["-m", "tuneassist.cli", "--tui"], False


def _desktop_dir() -> str:
    # XDG override (Linux), else ~/Desktop, else home.
    for cand in (os.environ.get("XDG_DESKTOP_DIR"),
                 os.path.join(os.path.expanduser("~"), "Desktop")):
        if cand and os.path.isdir(cand):
            return cand
    return os.path.expanduser("~")


# --------------------------------------------------------------------------
# per-OS writers (factored out so they're unit-testable without a real desktop)
# --------------------------------------------------------------------------
def _write_linux_desktop(path: str, target: str, args: list[str]) -> str:
    exec_line = " ".join([_q(target), *args])
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=AI-assisted engine tuning log analyzer\n"
        f"Exec={exec_line}\n"
        "Terminal=true\n"
        "Categories=Utility;Development;\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, 0o755)
    return path


def _write_macos_command(path: str, target: str, args: list[str]) -> str:
    cmd = " ".join([_q(target), *args])
    content = "#!/bin/bash\n" + cmd + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, 0o755)
    return path


def _q(s: str) -> str:
    return f'"{s}"' if " " in s else s


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------
def install_shortcut(dest_dir: str | None = None) -> tuple[bool, str]:
    target, args, _is_exe = _target_and_args()
    desktop = dest_dir or _desktop_dir()
    try:
        os.makedirs(desktop, exist_ok=True)
    except Exception as e:
        return False, f"Could not access the desktop folder: {e}"

    plat = sys.platform
    try:
        if plat.startswith("win"):
            return _install_windows(target, args, desktop)
        if plat == "darwin":
            p = _write_macos_command(os.path.join(desktop, f"{APP_NAME}.command"),
                                     target, args)
            return True, (f"Created {p}\nDouble-click it to launch the TUI. (First time: "
                          "right-click -> Open to clear the macOS warning.)")
        # linux / other unix
        p = _write_linux_desktop(os.path.join(desktop, f"{APP_NAME}.desktop"),
                                 target, args)
        # also register in the app menu if that dir exists / can be made
        menu_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
        try:
            os.makedirs(menu_dir, exist_ok=True)
            _write_linux_desktop(os.path.join(menu_dir, f"{APP_NAME}.desktop"), target, args)
        except Exception:
            pass
        return True, (f"Created {p}\nYou may need to right-click -> 'Allow Launching' the "
                      "first time. It should also appear in your app menu.")
    except Exception as e:
        return False, f"Could not create the shortcut: {e}"


def _install_windows(target: str, args: list[str], desktop: str) -> tuple[bool, str]:
    """Create a .lnk via the WScript.Shell COM object (no third-party deps)."""
    lnk = os.path.join(desktop, f"{APP_NAME}.lnk")
    arg_str = " ".join(args).replace("'", "''")
    tgt = target.replace("'", "''")
    workdir = os.path.dirname(target).replace("'", "''")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{tgt}'; "
        f"$s.Arguments = '{arg_str}'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        f"$s.IconLocation = '{tgt},0'; "
        "$s.Description = 'AI-assisted engine tuning log analyzer'; "
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=True, capture_output=True, timeout=30)
    except Exception as e:
        return False, f"Could not create the Windows shortcut: {e}"
    return True, f"Created {lnk}\nDouble-click it on your desktop to launch the TUI."
