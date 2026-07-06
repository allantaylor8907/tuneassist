# legacy/ — historical code retention

Per the project's retention policy: when a presentation layer is replaced, the
old code is preserved here rather than deleted.

## What lives here

The **Textual terminal UI** that was tuneassist's face from the first release
through **v0.1.21**, retired at the v2 cutover once the desktop GUI
(`tuneassist/gui/`, docs/V2.md) had been the default for several releases:

| file | what it was |
|---|---|
| `tui.py` | the full Textual app — garage, setup, analyze screens |
| `panels.py` | pure Rich renderable builders the TUI mounted |
| `demo.py` | locked-down demo entry for `textual serve` |
| `demo-serve/` | browser-hosted demo (`serve.py` + bundled sample logs) |
| `tests/` | the Textual pilot-harness tests for the above |

Retiring it let the shipped binary drop `textual` + `rich` entirely — smaller
download, faster startup.

## Running it anyway

The code was kept import-clean against the current engine. From the repo root:

    pip install textual rich          # the deps the core no longer carries
    python -c "from legacy.tui import run_tui; run_tui()"

The tests run the same way: `python legacy/tests/test_tui.py`. No guarantees —
this is frozen history, not maintained code. The last shipped binary with the
TUI built in is [v0.1.21](https://github.com/allantaylor8907/tuneassist/releases/tag/v0.1.21).
