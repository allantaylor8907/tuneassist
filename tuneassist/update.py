"""
update.py -- check GitHub Releases for a newer build and (for the packaged
single-file binary) self-update in place.

Design notes:
  * Offline-first. Every network path fails SILENTLY (returns None) on any error
    -- a street tuner with no signal must never see a traceback or a hang.
  * Stdlib only (urllib/json) -- no new dependencies, so the frozen binary stays
    small and the check works without pip.
  * Privacy: we only hit the network when the user runs an update command, or --
    at most once a day -- for a passive "a newer version exists" one-liner that
    can be turned off with TUNEASSIST_NO_UPDATE_CHECK=1.
  * Self-replacement only makes sense for the PyInstaller binary (sys.frozen).
    pip/pipx installs are told to use their package manager instead.
"""
from __future__ import annotations

import json
import os
import platform
import ssl
import sys
import time
import urllib.request
from dataclasses import dataclass

from . import __version__

REPO = "allantaylor8907/tuneassist"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
_TIMEOUT = 4.0
_CHECK_INTERVAL_S = 24 * 3600


@dataclass
class UpdateInfo:
    current: str
    latest: str
    page_url: str
    asset_url: str | None      # download URL of the binary for THIS OS, if any
    asset_name: str | None


# --------------------------------------------------------------------------
# version comparison
# --------------------------------------------------------------------------
def _parse(tag: str) -> tuple:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). Non-numeric pieces -> 0, so it degrades
    gracefully rather than raising on an odd tag."""
    s = (tag or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    out = []
    for part in s.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse(latest) > _parse(current)


# --------------------------------------------------------------------------
# OS / packaging detection
# --------------------------------------------------------------------------
def is_frozen() -> bool:
    """True when running as the PyInstaller single-file binary."""
    return bool(getattr(sys, "frozen", False))


def _os_asset_key() -> tuple[str, str]:
    """(substring that identifies this OS's asset, expected suffix)."""
    p = sys.platform
    if p.startswith("win"):
        return "windows", ".exe"
    if p == "darwin":
        return "macos", ""
    return "linux", ""


def _pick_asset(assets: list) -> tuple[str | None, str | None]:
    key, suffix = _os_asset_key()
    for a in assets:
        name = a.get("name", "").lower()
        if key in name and (suffix == "" or name.endswith(suffix)):
            return a.get("browser_download_url"), a.get("name")
    return None, None


# --------------------------------------------------------------------------
# the network call (always fail-silent)
# --------------------------------------------------------------------------
def _fetch_latest() -> dict | None:
    try:
        req = urllib.request.Request(
            _API_LATEST, headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": f"tuneassist/{__version__}"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def check_for_update() -> UpdateInfo | None:
    """Return UpdateInfo if a newer release exists, else None (incl. on any error
    or when offline)."""
    data = _fetch_latest()
    if not data:
        return None
    latest = data.get("tag_name") or data.get("name") or ""
    if not latest or not is_newer(latest, __version__):
        return None
    asset_url, asset_name = _pick_asset(data.get("assets", []))
    return UpdateInfo(__version__, latest.lstrip("vV"),
                      data.get("html_url") or _RELEASES_PAGE, asset_url, asset_name)


# --------------------------------------------------------------------------
# passive, throttled check (for a one-line startup notice)
# --------------------------------------------------------------------------
def _state_path() -> str:
    base = os.path.join(os.path.expanduser("~"), ".tuneassist")
    return os.path.join(base, "update.json")


def _read_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def passive_check(force: bool = False) -> UpdateInfo | None:
    """At most once a day, and never when disabled / in CI. Returns UpdateInfo to
    show a notice, or None. Caches the last seen latest tag so a notice persists
    between checks without re-hitting the network."""
    if not force:
        if os.environ.get("TUNEASSIST_NO_UPDATE_CHECK") or os.environ.get("CI"):
            return None
    st = _read_state()
    now = time.time()
    due = force or (now - float(st.get("last_check", 0)) > _CHECK_INTERVAL_S)
    if due:
        info = check_for_update()
        st["last_check"] = now
        st["latest"] = info.latest if info else __version__
        _write_state(st)
        return info
    # not due: surface a cached pending update if one was already found
    cached = st.get("latest")
    if cached and is_newer(cached, __version__):
        return UpdateInfo(__version__, cached, _RELEASES_PAGE, None, None)
    return None


# --------------------------------------------------------------------------
# self-update (frozen binary only)
# --------------------------------------------------------------------------
def cleanup_old_binary() -> None:
    """Remove leftovers from a Windows in-place update: the renamed '.old' binary
    and a '.new' download that didn't get swapped in (e.g. a previous failed/
    interrupted update). Keeps the user's folder clean and self-heals."""
    if not is_frozen():
        return
    for suffix in (".old", ".new"):
        p = sys.executable + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def _download(url: str, dest: str, progress=None) -> bool:
    """Stream `url` to `dest`. `progress(done_bytes, total_bytes)` is called as it
    goes (total is 0 if the server doesn't send Content-Length) so a UI can show a
    bar. Fail-silent: any error removes the partial file and returns False."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tuneassist"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r, \
                open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            if progress:
                progress(0, total)
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        return True
    except Exception:
        try:
            os.remove(dest)
        except Exception:
            pass
        return False


def download_asset(info: "UpdateInfo", progress=None) -> tuple[bool, str, str | None]:
    """Frozen-only: download the matching binary to '<exe>.new' (reporting
    progress), validating it's a real (multi-MB) file. Returns (ok, msg, path)."""
    if not is_frozen():
        return (False, "Not a packaged binary.", None)
    if info is None or not info.asset_url:
        return (False, "No binary for this OS in the latest release.", None)
    new = sys.executable + ".new"
    if not _download(info.asset_url, new, progress) or not os.path.exists(new) \
            or os.path.getsize(new) < 1_000_000:
        # a real binary is tens of MB; a tiny/missing file means the download was
        # blocked or truncated (often antivirus on an unsigned exe).
        try:
            os.remove(new)
        except Exception:
            pass
        return (False, "The download didn't complete -- antivirus may have blocked the "
                f"unsigned file. Get it manually: {info.page_url}", None)
    return (True, "Downloaded.", new)


def apply_update(info: "UpdateInfo", new: str) -> tuple[bool, str]:
    """Swap a freshly-downloaded '<exe>.new' into place. On Windows this hands off
    to a detached script (the caller must then exit so the file unlocks); on POSIX
    it's an atomic in-place replace. Returns (ok, message)."""
    exe = sys.executable
    try:
        if sys.platform.startswith("win"):
            return _windows_handoff(exe, new, info)
        os.chmod(new, 0o755)
        os.replace(new, exe)            # atomic on POSIX; running process keeps its inode
    except Exception as e:
        try:
            os.remove(new)
        except Exception:
            pass
        return (False, f"Could not install the update ({e}). Get it manually: {info.page_url}")
    return (True, f"Updated to v{info.latest}. Restart tuneassist to run the new version.")


def self_update(info: UpdateInfo | None = None) -> tuple[bool, str]:
    """Download the matching binary and swap it in place. Returns (ok, message).
    Only valid for the frozen binary; pip/pipx users are pointed at their tool."""
    if not is_frozen():
        return (False,
                "This is a source/pip install. Update with your package manager, e.g.\n"
                "    pipx upgrade tuneassist        (or: uv tool upgrade tuneassist)\n"
                "    pip install -U tuneassist")
    info = info or check_for_update()
    if info is None:
        return (True, f"You're on the latest version (v{__version__}). Nothing to update.")
    if not info.asset_url:
        return (False,
                f"v{info.latest} is available but has no binary for this OS.\n"
                f"Download manually: {info.page_url}")
    ok, msg, new = download_asset(info)
    if not ok:
        return (False, msg)
    # You can't reliably overwrite a running .exe in-process, so the Windows path
    # hands off to a detached script that waits for us to exit, swaps the binary
    # in, and relaunches -- the running exe is never touched, so a failure can't
    # brick the install. POSIX replaces in place.
    return apply_update(info, new)


def _ps_quote(p: str) -> str:
    """Single-quote a path for PowerShell (doubling any embedded single quotes)."""
    return "'" + str(p).replace("'", "''") + "'"


def _update_ps_script(exe: str, new: str, pid: int) -> str:
    """The Windows handoff, as PowerShell: WAIT for this process (by PID) to exit
    so the binary unlocks, then MOVE the new file onto it and relaunch. PowerShell
    + -LiteralPath handles spaces and parentheses (e.g. 'tuneassist (2).exe') that
    break a .bat, and waiting on the real PID is far more reliable than a timing
    guess. The new file is moved ONTO the exe path, so it always ends up '.exe'."""
    qexe, qnew = _ps_quote(exe), _ps_quote(new)
    lines = [
        "$ErrorActionPreference='SilentlyContinue'",
        "try { Wait-Process -Id " + str(int(pid)) + " -Timeout 60 } catch {}",
        "$ok=$false",
        "for ($i=0; $i -lt 40; $i++) {",
        "  Move-Item -LiteralPath " + qnew + " -Destination " + qexe + " -Force",
        "  if ($?) { $ok=$true; break }",
        "  Start-Sleep -Milliseconds 500",
        "}",
        "if ($ok) { Start-Process -FilePath " + qexe + " }",
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force",
    ]
    return "\r\n".join(lines) + "\r\n"


def _windows_handoff(exe: str, new: str, info: "UpdateInfo") -> tuple[bool, str]:
    """Write + launch a detached PowerShell script that waits for THIS process to
    exit, swaps the binary in, and relaunches it."""
    import subprocess
    import tempfile
    ps1 = os.path.join(tempfile.gettempdir(), "tuneassist_update.ps1")
    script = _update_ps_script(exe, new, os.getpid())
    try:
        with open(ps1, "w", encoding="utf-8") as f:
            f.write(script)
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File", ps1],
            close_fds=True, creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
    except Exception as e:
        try:
            os.remove(new)
        except Exception:
            pass
        return (False, f"Could not start the updater ({e}). Get it manually: {info.page_url}")
    return (True, f"Installing v{info.latest} -- the app will close and reopen "
                  "automatically in a moment.")


def relaunch() -> None:
    """Exit (and on POSIX, start the freshly-installed binary). Only meaningful
    for the frozen build. On Windows the update batch script handles the restart,
    so we just exit to release the file lock."""
    if not is_frozen():
        return
    try:
        if not sys.platform.startswith("win"):
            import subprocess
            subprocess.Popen([sys.executable, *sys.argv[1:]])
    except Exception:
        pass
    os._exit(0)
