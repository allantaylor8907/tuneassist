"""
demo.py -- the locked-down demo entry point for `textual serve`.

Hosting the Textual app over the web (zero install for the viewer) means the app
runs ON the server. So the demo build:
  * confines file access to the bundled sample logs (no server filesystem),
  * hides the native file picker / arbitrary folder browse,
  * uses a throwaway per-process garage so visitors never share or clobber state.

Run locally:    python -m tuneassist.demo
Serve on web:   pip install textual-serve
                textual serve "python -m tuneassist.demo"
                # then open http://localhost:8000
"""

from __future__ import annotations
import os
import tempfile


def _samples_dir() -> str | None:
    """Find the demo sample logs across source / installed layouts."""
    env = os.environ.get("TUNEASSIST_DEMO_SAMPLES")
    candidates = [env] if env else []
    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [
        os.path.join(here, "demo_samples"),                 # bundled in package
        os.path.join(here, "..", "demo", "samples"),        # repo layout
        os.path.join(os.getcwd(), "demo", "samples"),       # cwd
        os.path.join(here, "..", "tests", "fixtures"),      # fallback
    ]
    for c in candidates:
        if c and os.path.isdir(c) and any(f.endswith(".csv") for f in os.listdir(c)):
            return os.path.abspath(c)
    return None


def run_demo() -> None:
    from .tui import TuneAssistApp
    samples = _samples_dir()
    # throwaway garage so each served session is isolated
    tmp = tempfile.NamedTemporaryFile(prefix="tuneassist-demo-", suffix=".json",
                                      delete=False)
    tmp.close()
    TuneAssistApp(garage_path=tmp.name, demo=True, samples_dir=samples).run()


# `textual serve` invokes `python -m tuneassist.demo`
main = run_demo

if __name__ == "__main__":
    run_demo()
