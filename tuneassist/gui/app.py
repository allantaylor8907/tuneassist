"""
gui/app.py -- launch the tuneassist GUI.

Window strategy (docs/V2.md): Windows 10/11 always ship Edge, and
`msedge --app=URL` opens a chromeless app window -- a native-feeling shell with
ZERO extra Python dependencies. Fallback: the default browser. `--dev` skips the
window and prints the URL so you can iterate with devtools.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

from .server import start_server, serve_until_closed


def _find_edge() -> str | None:
    for c in (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
              os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
              shutil.which("msedge")):
        if c and os.path.isfile(c):
            return c
    return None


def open_window(url: str) -> bool:
    """Open `url` as an app window (chromeless) when possible."""
    if sys.platform.startswith("win"):
        edge = _find_edge()
        if edge:
            try:
                subprocess.Popen([edge, f"--app={url}", "--window-size=1380,900"],
                                 close_fds=True)
                return True
            except OSError:
                pass
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def run_gui(garage_path: str | None = None, dev: bool = False) -> None:
    httpd, url, state = start_server(garage_path)
    if dev:
        print(f"tuneassist GUI (dev): {url}")
        print("Serving until Ctrl+C (no heartbeat shutdown in dev mode).")
        try:
            while True:
                import time
                time.sleep(3600)
        except KeyboardInterrupt:
            httpd.shutdown()
        return
    if not open_window(url):
        print(f"Couldn't open a window -- browse to {url}")
    serve_until_closed(httpd, state)


if __name__ == "__main__":
    run_gui(dev="--dev" in sys.argv)
