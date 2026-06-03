# CLAUDE.md — context for Claude Code

You are continuing a project that was prototyped and validated in a chat session.
This file is your briefing. Read DESIGN.md next for the *why* behind every tuning
decision — that reasoning is hard-won and must not be re-derived or contradicted.

## What this is
An AI-assisted **analysis and recommendation** tool for engine tuning. It reads
datalogs from a running (or not-yet-running) vehicle and recommends changes the
human applies in the vendor software. It is **recommendation-only by design** —
it never writes a tune file, never touches the ECU, never automates the vendor
software. That boundary is both legal (vendor file formats are closed; no public
API) and a safety requirement (autonomous closed-loop writes to an engine can
hurt it). Do not add a "write the tune" feature.

## Platforms
- **GM / HPTuners** (Gen 3 & 4 LS — P01/P59, E38, E40, E67). Speed-density VE
  table + MAF, conventional. NOT the 2019+ Global B neural-net airflow model.
- **Holley EFI** (Terminator X, Sniper). Self-learning, integrated wideband.

## Hard constraint: native binary logs are NOT parseable
HPTuners `.hpl` and Holley `.dl` are binary float streams that do **not** contain
readable channel names — the channel-to-column mapping lives in the vendor
software. We tried; only headers/timestamps are recoverable. **Always ingest the
CSV the vendor software exports** (VCM Scanner: Scan → Export Data; Holley EFI
software: export to CSV). Never blind-guess columns from a binary log — a
mis-mapped column on a fuel calc can lean out and damage a motor.

## Pipeline (see cli.py)
    detect format → ingest to DataFrame → TRIAGE (gate) → correct → report
Triage runs first, every time, and stops the pipeline if the engine isn't in a
tunable state.

## Module map
- `channels`/`engine_gm.py` — GM channel resolution + the proven VE correction +
  the narrowband-vs-wideband cross-check classifier. **This is tested, working
  code ported verbatim from the prototype. Refactor carefully; keep behavior.**
  Also: `offset_vs_shape()` (flat global-offset vs VE table-shape detector), and
  `analyze()` now populates the `recommendation`/`wb_dev` cross-check grids.
  `maf_correction()` builds the frequency-indexed MAF-curve correction (DESIGN §9).
- `triage.py` — vehicle-state preflight (NO_CRANK … RUNNING_DRIVE). Tested.
- `holley.py` — Holley CSV ingest + Learn/CL-comp-based correction.
- `spark.py` — knock-governed timing analysis (DESIGN §10). Refuses without a knock
  channel; PULLs on knock (+margin), cautious opt-in ADDs for power; flags LEAN/HOT
  root causes. Tested.
- `diagnostics.py` — **pattern-based symptom→cause→correction engine** (DESIGN
  §12). `diagnose(df, col, cfg) → [Finding]`: lean/rich cruise, vacuum leak, bank
  imbalance, WB-vs-NB, WOT lean (critical) / rich (opportunity), injector duty,
  knock, temps, trim oscillation. Each detector degrades if channels absent;
  findings ranked critical→info. Strings are ASCII (safe for `--json`). Tested.
- `cams.py` — optional cam specs → conservative idle/timing starting points
  (DESIGN §11). Classify stock/mild/big; starting-point guidance only. Tested.
- `profile.py` — optional engine profile (block material, compression, power
  adder) → tailored spark ceiling + "pull timing back when…" checklist. Feeds
  `spark.analyze_spark(profile=…)`. Iron/low-CR vs alum/high-CR diverge here. Tested.
- `stages.py` — the tuning *journey* state machine (pure logic, tested):
  `summarize()` digests an analysis Result, `determine_stage()` maps
  (triage + digest) → journey stage, `prescribe()` → the concrete next move
  (what to change, what drive to do, what to log). No terminal deps.
- `core.py` — **the headless analysis engine; NO UI deps.** `analyze_log(path,
  opts) → CoreResult` orchestrates ingest→triage→analyze→spark→maf→stage→
  prescribe. `CoreResult.to_dict()` is the stable JSON contract + regression
  oracle. Owns `SessionOpts`, `detect_platform`, `ingest`, `_HolleyResult`,
  `_blocked_prescription`, and the garage codec `opts_to_record`/`record_to_opts`.
- `panels.py` — **pure Rich renderable *builders*** (no printing/IO): banner,
  journey bar, triage, heatmaps, cross-check, spark, MAF, safety, prescription,
  and `build_report(cr, …)` (the whole result as one Group). Shared by both UIs.
- `render.py` — thin Rich-Console adapter that *prints* `panels.*` for the
  classic wizard. No construction logic; keeps wizard output unchanged.
- `tui.py` — **the Textual app** (`run_tui`): GarageScreen (pick/new/rename/
  delete) → SetupScreen (form) → AnalyzeScreen (log input → `panels.build_report`
  + journey bar). Pure consumer of `core`; mounts shared `panels.*` in Static
  widgets. Tested via the Textual pilot harness.
- `wizard.py` — the classic Rich guided session: a *thin renderer* over
  `core.analyze_log`. IO goes through `WizardIO` so the flow is scriptable in
  tests. Asks setup once, remembers it for the session, persists per-vehicle via
  `garage`. Re-exports core helpers for back-compat.
- `garage.py` — on-disk per-vehicle memory (`~/.tuneassist/garage.json`): pure
  load/save/list/get/upsert, no package deps. Tolerates missing/corrupt files.
  Tests pass a temp `garage_path` so they never touch real home. Tested.
- `cli.py` — orchestrator. No args/log path → Rich wizard; `--tui` → Textual app;
  `--batch` → plain report; `--json` → headless structured output.

## Architecture / distribution
- **UI is decoupled from the engine.** `core.py` is headless (data in → data out);
  `wizard.py`/`render.py` are a presentation layer over it. Build new UIs against
  `core.analyze_log(...).to_dict()`, never by importing render/wizard.
- **Packaging is proven:** PyInstaller `--onefile` from `packaging/entry.py`
  yields a ~32 MB single binary that runs the whole pipeline with no Python
  installed (validated on Windows). `.github/workflows/build.yml` builds
  win/mac/linux binaries and attaches them to GitHub Releases on `v*` tags.
  Also `pipx install .` / `uv tool install .` (console script `tuneassist`).
- Headless contract: `python -m tuneassist.cli LOG.csv --json [--spark] [--airflow …]`.
  Keep `core.to_dict()` stable; `tests/test_core.py` is its oracle.

## Conventions
- Python 3.11+, pandas/numpy, no heavy deps. matplotlib optional (heatmaps).
- MAP is normalized to **kPa** internally; HPTuners often logs psi (×6.894757)
  or inHg (×3.386389). Holley logs kPa.
- Corrections are **multipliers** (1.05 = +5%), damped by `Config.damping` (0.70)
  so the user doesn't over-correct and oscillate. Cells below
  `Config.min_samples` are low-confidence and blanked.
- Tests live in `tests/`, run with `python tests/test_triage.py` (or pytest).
  Real logs go in `tests/fixtures/` as regression fixtures — see DESIGN.md for
  what each known log should produce.

## Status / next tasks if asked to continue
DONE: interactive guided session (wizard/render/stages); cross-check grid wired
into `analyze`; global-offset-vs-table-shape detector (`offset_vs_shape`);
VE→MAF→WOT→spark journey with airflow-mode awareness; `maf_correction` (Hz table);
knock-governed `spark.py`; optional cam specs (`cams.py`). Journey ladder:
GET_RUNNING → STABILIZE_IDLE → DIAL_IDLE_CRUISE → TUNE_VE_SD → TUNE_MAF →
TUNE_POWER → TUNE_SPARK → CONVERGED. Domain reasoning in DESIGN.md §9–11.
DONE since: core decoupled (`core.analyze_log`/`to_dict`), single-binary
packaging proven (PyInstaller `--onefile`, ~34 MB with Textual; CI builds 3 OSes),
and the **Textual UI** (`tui.py`) shipped — garage/setup/analyze screens reusing
`panels.*`. Decision settled: stay Python + ship binaries; revisit a Rust+Polars
port only if PyInstaller binaries prove unacceptable for end users.
Remaining:
0. Textual UI polish: a DataTable view of the correction grid (sortable/clickable
   cells) instead of a Rich heatmap in a Static; live file-watch to auto-analyze a
   new log; `textual serve` web demo. The flow + screens are done and tested.
1. DONE — Holley validated on a real Terminator X CSV (`tests/fixtures/
   holley_sample.csv`, `tests/test_holley.py`). Fixed: latin-1 + units-row in the
   loader, `MAP`/`TPS` vs `… RoC`, time=`RTC`, knock pattern, Holley-correct
   diagnosis (no narrowband finding; lean-but-on-target WOT → power opportunity)
   and base-fuel prescription language. Remaining Holley: Sniper/Dominator label
   variants, and spark on Holley still borrows GM patterns (works on this file).
2. `prescribe("TUNE_MAF", …)` text reads as a VE→MAF transition even when the user
   is already in MAF mode with residual cruise error — reword to be mode-neutral.
3. LT (Gen 5, DI) airflow model + Holley LT — deferred, different airflow model.
DONE since: per-vehicle disk persistence (`garage.py`) — each vehicle remembers
hardware/fuel/airflow + journey history across launches; setup asked once/session.
