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
Three independent axes (see **docs/PLATFORMS.md**): **platform** (tuning
software/log format — HP Tuners, Holley), **make** (GM, Ford, Mopar …), and
**architecture** (gm_gen3_4_ls, ford_coyote, …) where the airflow/spark strategy
lives. The internal `platform` value is still `"gm"`/`"holley"` for back-compat
(`"gm"` == the HP Tuners platform legacy value); `core.platform_label()` renders
"HP Tuners". `make`/`architecture` are new optional `SessionOpts`/garage/to_dict
fields, auto-detected (`detect_make`) or user-picked. Phase 2 will extract the
GM-LS analysis into an architecture strategy module.
- **HP Tuners** (Gen 3 & 4 LS — P01/P59, E38, E40, E67). Speed-density VE
  table + MAF, conventional. NOT the 2019+ Global B neural-net airflow model.
- **Holley EFI** (Terminator X, Sniper). Self-learning, integrated wideband.
- Ethanol content is auto-detected from a flex-fuel channel (`stoich_from_ethanol`).

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
  §12). `diagnose(df, col, cfg, platform, profile) → [Finding]`: lean/rich cruise,
  vacuum leak, bank imbalance, WB-vs-NB (GM-only), WOT shortfall/lean/rich,
  injector duty, trims-clipping, low-voltage, low-fuel-pressure, knock, temps,
  trim oscillation, a **forced-induction** set
  (boost-lean, fuel-pressure-drop, MAP-sensor-range, CL-in-boost, boost-IAT), a
  **cold-start/warmup** set (thermostat, warmup rich/lean, enrichment-not-decayed
  with neutral-baseline detection), and an **idle-quality** set (hunt, off-target
  vs logged idle target, idle AFR, IAC-closed, idle-timing swing). Detectors
  degrade if channels absent; findings ranked critical→info and shown as a
  readable "What I see / Likely / Do this" panel. ASCII-safe. Tested.
- `tables.py` — maps a recommended change → the **exact vendor table** to edit
  (GM HP Tuners table names + Holley table names). `core._name_tables` appends
  these to findings; `_primary_change_finding` names the lead table inline.
- `cams.py` — optional cam specs → conservative idle/timing starting points
  (DESIGN §11). Classify stock/mild/big; starting-point guidance only. Tested.
- `profile.py` — optional engine profile (block material, compression, power
  adder, mods) → tailored spark ceiling + "pull timing back when…" checklist.
  Feeds `spark.analyze_spark(profile=…)`. Iron/low-CR vs alum/high-CR diverge here.
  `ENGINE_PRESETS` (Chevy LS/SBC/BBC, Ford, Pontiac) + `COMMON_MODS` +
  `preset_to_profile()` drive the TUI's pick-an-engine setup. Tested.
- `stages.py` — the tuning *journey* state machine (pure logic, tested):
  `summarize()` digests an analysis Result, `determine_stage()` maps
  (triage + digest) → journey stage, `prescribe()` → the concrete next move
  (what to change, what drive to do, what to log). No terminal deps.
- `core.py` — **the headless analysis engine; NO UI deps.** `analyze_log(path,
  opts) → CoreResult` orchestrates ingest→triage→analyze→spark→maf→stage→
  prescribe. `CoreResult.to_dict()` is the stable JSON contract + regression
  oracle. Owns `SessionOpts`, `detect_platform`, `ingest`, `_HolleyResult`,
  `_blocked_prescription`, the garage codec `opts_to_record`/`record_to_opts`,
  `_primary_change_finding` (the lead "apply this" item), `_annotate_safety_
  resolution` (does the change fix the safety issue?), and `_apply_mod_insights`
  (bolt-ons explain the data — DESIGN §13).
- `panels.py` — **pure Rich renderable *builders*** (no printing/IO): banner,
  journey bar, triage, heatmaps, cross-check, spark, MAF, safety, prescription,
  and `build_report(cr, …)` (the whole result as one Group). Used by the TUI and
  the plain `--batch`/`cli.run` report path.
- `tui.py` — **the Textual app** (`run_tui`), the one interactive UI: GarageScreen
  (pick/new/rename/delete) → SetupScreen (form) → AnalyzeScreen (log input →
  `panels.build_report` + journey bar). Pure consumer of `core`; mounts shared
  `panels.*` in Static widgets. Tested via the Textual pilot harness.
  (The old Rich `wizard.py`/`render.py` were removed — TUI + `--batch`/`--json`
  cover interactive + headless.)
- `garage.py` — on-disk per-vehicle memory (`~/.tuneassist/garage.json`): pure
  load/save/list/get/upsert, no package deps. Tolerates missing/corrupt files.
  Tests pass a temp `garage_path` so they never touch real home. Tested.
- `demo.py` — locked-down demo entry for `textual serve` (`run_demo`): confines
  file access to bundled `demo/samples/`, hides the native picker, throwaway
  garage. `demo/serve.py` hosts it in a browser. Tested.
- `update.py` — **self-update / release check** (stdlib only, offline-safe). Hits
  the GitHub Releases API, compares `__version__` to the latest tag, and for the
  frozen PyInstaller binary downloads the OS-matched asset and swaps it in place
  (Windows: rename-running-exe trick + `.old` cleanup next launch; POSIX: atomic
  rename). `passive_check()` is throttled to once/day (state in
  `~/.tuneassist/update.json`), disabled by `TUNEASSIST_NO_UPDATE_CHECK=1`/`CI`,
  and every network path fails silently. pip/pipx installs are pointed at the
  package manager. Tested (all offline).
- `submit.py` — **opt-in log submission** (off until `SUBMIT_URL` is set, stdlib
  only). After an analysis the TUI ("Share log" button + `s`) offers to
  bundle ONLY the analyzed log + a non-identifying `submission.json` (version,
  platform, stage, profile, summary, finding ids, user-typed note/contact — never
  the garage/nickname) into `~/.tuneassist/submissions/*.zip`, then open a free
  upload form (`docs/SUBMISSIONS.md`; Tally recommended). Never auto-sends; the
  user attaches the file themselves. Tested.
- `update.py` self-update also exposes `relaunch()` (start the swapped binary,
  exit this one). The TUI shows a one-click "Update & restart" banner on the
  garage screen when a newer release is found, and Ctrl+U installs + relaunches.
- `shortcut.py` — **desktop launcher creator** (`--install-shortcut`, stdlib
  only). Drops an OS-native double-clickable shortcut that opens the TUI: Windows
  `.lnk` via WScript.Shell COM, macOS `.command`, Linux `.desktop` (+ app-menu
  entry). Resolves the launch target for frozen binary vs pip/console-script vs
  `python -m`. Writers factored for testing; paths with spaces quoted. Tested.
- `gui/` — **the v2 desktop GUI** (docs/V2.md): `server.py` is a stdlib-only
  localhost HTTP server (random port + URL token, heartbeat lifecycle) exposing a
  JSON API over core/garage/update/submit; `app.py` opens it in a chromeless
  Edge app window (zero new deps; falls back to the default browser);
  `static/` is a no-build-step HTML/CSS/JS frontend with vendored ECharts —
  light/dark themes (dark default), journey stepper, verdict hero, findings
  cards, VE heatmap, MAF row, log timeline with knock markers AND lean/rich
  danger shading (markArea from `timeseries.bands`), TSV copy, whole-window
  drag-drop (overlay), native Browse via a TopMost-owner PowerShell dialog
  (plain ShowDialog hides behind the chromeless window). A motion layer
  (view fades, staggered report/finding reveals, journey pulse) honors
  `prefers-reduced-motion`. Brand: the heatmap-grid mark (`static/favicon.svg`
  + inline sidebar SVG, two-tone wordmark). **The GUI is the no-args default
  since the v0.1.14 cutover** — `_hide_own_console()` hides the double-click
  console (only when we're its sole owner). The TUI stays reachable via `--tui`
  for one transition release, then TUI/panels/demo move to `legacy/`. Setup is a
  fitment cascade (`fitment.py`): HP Tuners → make → generation → engine; Holley
  → product → make → engine — only real combinations (tests/test_fitment.py
  enforces); the muscle-car roster (SBC/BBC, Pontiac/Buick/Olds/AMC classics,
  FE/Cleveland Fords, B/RB Mopars) lives under Holley since those have no factory
  ECU. **Beginner onboarding** (`app.js` `firstLogContent`/`enterFirstLog`): an
  empty garage shows a guided hero; the flow walks add-a-car → a platform-aware
  "capture your first log" guide (channels to log, export-to-CSV steps, "grab a
  baseline, change nothing yet") before analysis. Tested via in-process HTTP
  (test_gui).
- `cli.py` — orchestrator. No args → the v2 GUI (the default, so the downloaded
  binary opens the app on double-click); a bare log path or `--batch` → plain
  text report (`cli.run`, for SSH/scripts); `--tui` → classic Textual app;
  `--demo` → locked-down demo; `--json` → headless JSON. `--version`,
  `--check-update`, `--update`, `--install-shortcut` (launcher opens the
  default = GUI); a throttled one-line update notice precedes the report/batch
  flows. Version is single-sourced in `tuneassist/__init__.py` (`__version__`);
  pyproject reads it dynamically.

## Architecture / distribution
- **UI is decoupled from the engine.** `core.py` is headless (data in → data out);
  the TUI / `--batch` report are a presentation layer over it. Build new UIs against
  `core.analyze_log(...).to_dict()`, never by importing the TUI.
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
0. DONE — DataTable grid (interactive + sortable), quick-scan bypass, native
   file picker / browse-anywhere, Textual themes (Ctrl+T, gruvbox/nord/…,
   persisted), ASCII-art logo banner. Remaining TUI polish: live file-watch to
   auto-analyze a new log; `textual serve` web demo.
1. DONE — Holley validated on a real Terminator X CSV (`tests/fixtures/
   holley_sample.csv`, `tests/test_holley.py`). Fixed: latin-1 + units-row in the
   loader, `MAP`/`TPS` vs `… RoC`, time=`RTC`, knock pattern, Holley-correct
   diagnosis (no narrowband finding; lean-but-on-target WOT → power opportunity)
   and base-fuel prescription language. Remaining Holley: Sniper/Dominator label
   variants, and spark on Holley still borrows GM patterns (works on this file).
2. Validated on **Sniper V2** (`tests/fixtures/sniper_sample.csv`): same Holley
   loader works; no knock channel; `Fuel Press Switch` excluded from `fuelpres`;
   `speed`/`battery` resolve. Idle detection now uses a shared `_idle_mask`
   (MAP-window + vehicle-speed) so decel/coast no longer false-flags IDLE_HUNT
   (trimmed std, threshold 90).
3. `prescribe("TUNE_MAF", …)` text reads as a VE→MAF transition even when the user
   is already in MAF mode with residual cruise error — reword to be mode-neutral.
3. LT (Gen 5, DI) airflow model + Holley LT — deferred, different airflow model.
DONE since: per-vehicle disk persistence (`garage.py`) — each vehicle remembers
hardware/fuel/airflow + journey history across launches; setup asked once/session.
