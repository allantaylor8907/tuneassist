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
- `crank.py` — **crank/no-start diagnosis**. When triage returns CRANKING_NO_START
  or STARTED_STALLED, `diagnose_no_start(df, col, cfg, state)` reads the crank
  window and explains WHY it won't catch: injector pulse (NOSTART_NO_INJECTION),
  fuel pressure, wideband flooded/starved, spark/sync confirmable, slow crank, or
  a "log these" catch-all. Returns ranked `diagnostics.Finding`s; `core` runs it
  in the `not can_correct` branch so a no-start log still gets real findings.
  Tested (synthetic crank logs).
- `holley.py` — Holley CSV ingest + Learn/CL-comp-based correction.
- `spark.py` — knock-governed timing analysis (DESIGN §10). Refuses without a knock
  channel; PULLs on knock (+margin), flags LEAN/HOT root causes. Power ADDs
  toward MBT (safe WOT region) are ALWAYS computed now; `find_power` is only
  the DEFAULT reveal -- the GUI's "Add power" toggle shows/hides them client-
  side (default hidden, pulling is the safe view). `to_dict` emits pulls-only
  `tsv.spark`/`spark_abs` plus `*_power` variants with the adds applied; the
  toggle picks. Persisted per car via find_power on the next analyze. **Table-aware** when the user pastes their spark
  table WITH values (`SessionOpts.tables["spark"]`): recommendations turn absolute
  (current -> target per cell), ADDs cap at `profile.spark_bounds()`'s ceiling
  (AT_CEILING action), `scan_spark_table` sanity-checks the table itself (WOT
  cells above the build ceiling; cam-aware idle-region check via cams guidance),
  and a per-cell delivered-vs-table deficit note localizes what knock/IAT/torque
  management is eating. `core.spark_abs_tsv` emits the COMPLETE new table
  (uncovered cells keep the ORIGINAL value, never 0) for a plain full-table
  paste; `tsv.spark` stays deltas for Paste Special -> Add. Tested.
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
- `symptoms.py` — **offline "what's it doing?" complaint matcher** (no network,
  no model, by explicit user requirement -- maybe a tiny bundled local model
  someday, never an API). A curated symptom taxonomy (rough idle, bog, knock,
  rich/lean, hard start, surge, misfire, boost, cold running...) with synonym
  regexes: `match(text)` recognizes the complaint, `region_coverage(df,col,cfg)`
  checks which log regions (idle/wot/cruise/crank/boost/warmup) have samples,
  `relate`/`reorder` pin the findings that speak to the complaint FIRST and emit
  honest coverage gaps ("you said it stumbles at WOT but this log never gets
  above 60 kPa -- capture a pull"). A prior over diagnostics, never a source:
  it cannot invent findings. `SessionOpts.complaint` -> `to_dict()['complaint']`
  = {text, matched, related_ids, gaps}; CLI `--complaint`; the GUI's analyze view
  has the box (Win+H dictation works for free), the report shows a "Heard you"
  card + tags matched findings, and the car remembers the last complaint
  (record['complaint'], persisted on analyze, prefills the box). Tested.
- `tables.py` — maps a recommended change → the **exact vendor table** to edit
  (GM HP Tuners table names + Holley table names). `core._name_tables` appends
  these to findings; `_primary_change_finding` names the lead table inline.
- `channels_ref.py` — the canonical **"what to log" reference** per platform/
  generation (Gen 3 / Gen 4 / Gen 5 / Holley), each channel tagged
  essential/recommended/reference with the internal resolver `key` + a `why`.
  `reference()` feeds the GUI "Channels to log" popout (and the onboarding guide);
  `coverage(col, platform, architecture)` checks a resolved log's columns and
  returns present/missing (with why) → `CoreResult.channel_coverage` → the report's
  coverage strip ("add these before your next log"). Holley keys are Holley's
  resolver canonicals (cts/mat/ign/afr_target/cl_comp/learn). Tested.
- `cams.py` — optional cam specs → conservative idle/timing starting points
  (DESIGN §11). Classify stock/mild/big; starting-point guidance only. Tested.
- `profile.py` — optional engine profile (block material, compression, power
  adder, mods) → tailored spark ceiling + "pull timing back when…" checklist.
  Feeds `spark.analyze_spark(profile=…)`. Iron/low-CR vs alum/high-CR diverge here.
  `ENGINE_PRESETS` (Chevy LS/SBC/BBC, Ford, Pontiac) + `COMMON_MODS` +
  `preset_to_profile()` drive the GUI's pick-an-engine setup. Tested.
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
  (bolt-ons explain the data — DESIGN §13). **Custom VE table axes**
  (`SessionOpts.ve_axes` = `{rpm:[…], map:[…]}`, per-vehicle, garage-persisted):
  the user pastes their real VE/base-fuel table breakpoints (we can't read the
  binary tune) and `analyze_log` bins the correction onto exactly those axes via
  a *copied* cfg (so spark/MAF keep their own default axes), snapping each sample
  to the nearest breakpoint (`_axis_edges` midpoints), relabels the grid to the
  breakpoint values (`_relabel_to_breakpoints`), and `correction_tsv` transposes
  to VCM Editor layout (RPM cols × MAP rows) so Paste Special lines up
  cell-for-cell. `parse_axis` preserves the user's paste order (Holley MAP is
  descending, VCM ascending); `parse_ve_table` reads a whole "Copy with Axis"
  paste (RPM header row + MAP-led data rows) so the GUI takes one paste instead
  of typed breakpoints; `clean_ve_axes` accepts `{table}` or `{rpm,map}`. **Full tune tables**
  (`SessionOpts.tables` = ve/spark/maf via `clean_tables`): `parse_ve_table` also
  captures the cell VALUES; `parse_maf_table` reads the 1-D MAF calibration
  (column-pairs or row-pair); `table_slots(platform, arch)` names what the user
  sees (Gen 3 Main VE / Gen 4+ VVE / Holley Base Fuel + Timing; MAF is
  HPT-only). Tables persist in the garage; axes derive from them automatically.
  The GUI server versions them: an upsert with CHANGED values archives the prior
  copy to `table_history` (cap 10) and resets `analyses_since_paste`; each
  analyze of a saved car increments it, and `to_dict`'s `tables_meta.stale`
  (>=2) drives the GUI's "re-paste your tables" banner. `server._table_diffs`
  diffs the current tables against the newest `table_history` entry per slot
  (`core.diff_table` -- cells matched by breakpoint VALUE, so an axis edit just
  drops the moved cells) -> `table_diffs` on the vehicle record -> the setup
  section's "since your last paste" expander. **Table-aware VE** (mirrors the
  spark treatment): with the VE table's VALUES on file, to_dict correction
  cells carry absolute current->target (`correction.has_table`), the heatmap
  tooltip shows "now X -> set Y", and `ve_abs_tsv` emits the COMPLETE new VE
  table (uncovered cells keep the ORIGINAL value) as `tsv.ve_abs` for a plain
  full-table paste ('Copy new VE table').
- `legacy/` (repo root) — **the retired Textual TUI**, preserved per the
  retention policy: `tui.py`, `panels.py`, `demo.py`, `demo-serve/`, and their
  pilot-harness tests. Retired at the v2 cutover (last shipped in v0.1.21) so
  the frozen binary could drop `textual`+`rich`. Kept import-clean against the
  current engine (see legacy/README.md); NOT in the wheel/binary or CI suite.
- `garage.py` — on-disk per-vehicle memory (`~/.tuneassist/garage.json`): pure
  load/save/list/get/upsert, no package deps. Tolerates missing/corrupt files.
  Tests pass a temp `garage_path` so they never touch real home. Tested.
- `update.py` — **self-update / release check** (stdlib only, offline-safe). Hits
  the GitHub Releases API, compares `__version__` to the latest tag, and for the
  frozen PyInstaller binary downloads the OS-matched asset and swaps it in place
  (Windows: rename-running-exe trick + `.old` cleanup next launch; POSIX: atomic
  rename). Split into `download_asset(info, progress)` (streams to `<exe>.new`,
  reports bytes via callback) + `apply_update(info, new)` (the swap/handoff);
  `self_update` chains them for the CLI. **The GUI drives these in a background
  worker** (`server._run_update_worker`): `/api/update/install` starts it,
  `/api/update/progress` is polled for a bar, and on a successful frozen apply the
  worker calls `relaunch()` so the **process actually exits** — that's what
  unlocks the .exe so the Windows handoff can swap + relaunch (the prior hang was
  the server never exiting, so `Wait-Process` timed out). `passive_check()` is
  throttled to once/day (state in `~/.tuneassist/update.json`), disabled by
  `TUNEASSIST_NO_UPDATE_CHECK=1`/`CI`, and every network path fails silently.
  pip/pipx installs are pointed at the package manager. Tested (all offline).
- `submit.py` — **opt-in log submission** (off until `SUBMIT_URL` is set, stdlib
  only). After an analysis the GUI (`/api/submit`) offers to
  bundle ONLY the analyzed log + a non-identifying `submission.json` (version,
  platform, stage, profile, summary, finding ids, user-typed note/contact — never
  the garage/nickname) into `~/.tuneassist/submissions/*.zip`, then open a free
  upload form (`docs/SUBMISSIONS.md`; Tally recommended). Never auto-sends; the
  user attaches the file themselves. Tested.
- `update.py` also exposes `relaunch()` (start the swapped binary, exit this
  one); the GUI's Settings card is the one-click "Update & restart" surface.
- `shortcut.py` — **desktop launcher creator** (`--install-shortcut`, stdlib
  only). Drops an OS-native double-clickable shortcut that opens the app: Windows
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
  since the v0.1.14 cutover; the TUI was retired to `legacy/` in v0.1.22.**
  Console handling is layered: the Windows build passes `--hide-console
  hide-early` so the PyInstaller BOOTLOADER hides the console before onefile
  extraction (no black flash on double-click; terminal launches keep full
  output), with `_hide_own_console()` (sole-owner check) as a Python-level
  backstop. A branded `packaging/splash.png` shows during extraction
  (`--splash`); `cli._close_splash()` drops it immediately for console paths
  and `gui/app._close_splash()` hands it off once the window opens. The exe
  carries `packaging/icon.ico` (multi-size, the heatmap mark). Setup is a
  fitment cascade (`fitment.py`): HP Tuners → make → generation → engine; Holley
  → product → make → engine — only real combinations (tests/test_fitment.py
  enforces); the muscle-car roster (SBC/BBC, Pontiac/Buick/Olds/AMC classics,
  FE/Cleveland Fords, B/RB Mopars) lives under Holley since those have no factory
  ECU. **Beginner onboarding** (`app.js` `firstLogContent`/`enterFirstLog`): an
  empty garage shows a guided hero; the flow walks add-a-car → a platform-aware
  "capture your first log" guide (channels to log, export-to-CSV steps, "grab a
  baseline, change nothing yet") before analysis. Tested via in-process HTTP
  (test_gui).
- `cli.py` — orchestrator. No args → the desktop GUI (the default, so the
  downloaded binary opens the app on double-click); a bare log path or
  `--batch` → plain text report (`cli.run`, for SSH/scripts); `--json` →
  headless JSON. `--tui`/`--demo` are retired stubs that print where the old
  TUI lives (legacy/) and exit. `--version`,
  `--check-update`, `--update`, `--install-shortcut` (launcher opens the
  default = GUI); a throttled one-line update notice precedes the report/batch
  flows. Version is single-sourced in `tuneassist/__init__.py` (`__version__`);
  pyproject reads it dynamically.

## Architecture / distribution
- **UI is decoupled from the engine.** `core.py` is headless (data in → data out);
  the GUI / `--batch` report are presentation layers over it. Build new UIs
  against `core.analyze_log(...).to_dict()`, never by importing another UI.
- **Packaging is proven:** PyInstaller `--onefile` from `packaging/entry.py`
  yields a ~33 MB single binary (post-TUI-retirement; textual/rich gone, tcl/tk
  added for the splash) that runs the whole pipeline with no Python installed.
  `.github/workflows/build.yml` builds Windows (primary: icon + splash +
  `--hide-console hide-early`) and Linux binaries and attaches them to GitHub
  Releases on `v*` tags. macOS builds were dropped in v0.1.22 (the vendor
  tuning software is Windows-only); update.py stays graceful there.
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
0. DONE — and the whole Textual layer has since been RETIRED to legacy/
   (v0.1.22); the GUI is the only interactive UI. Historical items kept above
   for the record.
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
