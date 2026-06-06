# Contributing to tuneassist

Thanks for helping make this better. Anyone can contribute — here's how.

## Ground rules

- This tool is **recommendation-only by design**. It must never write a tune
  file, touch the ECU, or automate the vendor software. PRs that add "write the
  tune" features won't be accepted — see DESIGN.md for why.
- Keep it ASCII-safe (it runs in legacy Windows terminals) and dependency-light.

## How to submit a change

1. **Fork** the repo and create a branch off `main`.
2. Make your change. Add or update a test in `tests/`.
3. Run the suite: `for t in tests/test_*.py; do python "$t"; done` (or pytest).
4. Open a **pull request** against `main`.

CI runs the tests and builds the binaries on every PR. A maintainer reviews and
merges — only the repo owner can merge, but anyone is welcome to open a PR.

## The most valuable contribution: real logs

The detectors get smarter from real-world datalogs. If the tool got something
wrong on your car:

- In the app, use **Share log** (TUI) or say yes to the share prompt (wizard) to
  package the log + analysis summary, then upload it via the form, **or**
- Open an issue and attach the exported CSV with a note on what it got wrong and
  what was actually going on.

Either way, a confirmed-wrong log usually becomes a regression fixture in
`tests/fixtures/` — that's the loop that improves the analysis.

## Adding a platform or detector

- Channel resolution lives in `engine_gm.py` / `holley.py` (regex patterns).
- Detectors live in `diagnostics.py` (`diagnose()` runs them; each degrades
  gracefully when a channel is absent).
- The analysis is headless in `core.py`; UIs (`tui.py`, `wizard.py`) only consume
  it. Build against `core.analyze_log(...).to_dict()`, never the other way.

See CLAUDE.md for the module map and DESIGN.md for the tuning reasoning.
