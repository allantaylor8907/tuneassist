# Platforms, makes & engine architectures — the extensibility model

tuneassist analyzes logs across different tuning **platforms** (software/ECUs),
engine **makes** (OEMs), and engine **architectures** (airflow + spark strategy).
These are three independent axes, and tuning methodology genuinely differs along
each. This doc defines the model so new platforms/makes/engines drop in cleanly.

## The three axes (don't conflate them)

1. **Platform** — the tuning software / ECU and therefore the *log format*, the
   closed-loop semantics, and the *names of the tables you edit*.
   - `hptuners` (HP Tuners VCM) — spans GM, Ford, Dodge, …
   - `holley` (Terminator X, Sniper, Dominator)
   - future: SCT/Forscan, MS3/rusEFI, etc.
   - HP Tuners is ONE platform that covers many makes — which is exactly why
     "GM" was the wrong name for it.

2. **Make** — the engine OEM. Drives table naming nuances and which
   architectures are plausible.
   - `gm`, `ford`, `mopar`, `pontiac` (historical), …

3. **Architecture / family** — where the actual *tuning strategy* lives: the
   airflow model and spark behavior.
   - `gm_gen3_4_ls`  — speed-density VE table + MAF curve (the proven path)
   - `gm_gen5_lt`    — Gen 5 DI, different airflow model (deferred)
   - `ford_coyote`   — MAF / Absolute-Load based, lambda widebands, no manifold MAP
   - `mopar_hemi`    — …
   A given (platform, make) can host several architectures (an LS and an LT are
   both GM-on-HPTuners but tune differently).

A log is therefore described by `(platform, make, architecture)`, e.g. the
LeMans 5.7 = `(hptuners, gm, gm_gen3_4_ls)`; the F150 = `(hptuners, ford,
ford_coyote)`.

## Where knowledge lives (generic vs specific)

Keep the default **generic**; specialize only where the strategy truly differs.

- **Generic core (one implementation, parameterized by Config):** triage, the
  trim/wideband fueling math, the symptom→cause detectors (lean/rich, bank
  imbalance, knock, injector duty, trim clipping, temps, voltage, fuel pressure,
  idle quality, cold-start), the journey state machine, knock-governed spark.
  These should never branch on make.
- **Platform adapter (per platform):** ingest (channel-name regex + units +
  closed-loop status semantics) and output (exact table names). Today:
  `engine_gm.load_log/CHANNEL_PATTERNS` is the HP Tuners adapter; `holley.py` is
  the Holley adapter; `tables.py` holds per-platform table names.
- **Architecture strategy (per engine family):** the airflow model (SD VE table
  vs MAF curve vs DI vs Ford load%), what "the correction grid" maps to, the
  spark ceiling defaults, and idle strategy. This is the pluggable seam new
  engines hook into.

Detectors degrade gracefully when a channel is absent, so a new make that lacks
(say) a MAF still gets every applicable generic finding for free.

## How tests should be organized

- **Generic detector tests** — synthetic, platform-agnostic (most of
  `test_diagnostics.py`). Adding a make must not require touching these.
- **Platform ingest tests** — real exported logs as fixtures, asserting channels
  resolve and units normalize (`test_holley.py`, the HP Tuners cases in
  `test_core.py`).
- **Architecture strategy tests** — the VE/MAF/spark behavior for a family.
- Every "the tool got it wrong on my log" report becomes a fixture under the
  right `(platform, make, architecture)` — that's the regression flywheel.

## Adding a new … (the contributor contract)

- **New platform** (e.g. SCT): add an ingest adapter (channel map + units +
  CL semantics) and a table-name map. The generic detectors light up
  automatically.
- **New make** (e.g. Mopar on HP Tuners): usually just channel-name aliases +
  table names; reuse an existing architecture if the airflow model matches.
- **New architecture** (e.g. Ford Coyote): implement the airflow/spark strategy
  behind the family interface; the rest is reused.

## Migration plan (incremental, back-compat)

The word `platform` currently means `{gm, holley}` and appears in the stable
JSON contract (`core.to_dict()`) and in on-disk garage records — so it can't be
renamed blindly.

- **Phase 1 (additive, no breakage):** introduce `make`/`architecture` as new,
  optional fields; relabel the UI to say **HP Tuners** (platform) + a make
  picker; auto-detect fuel from an Ethanol-% channel. Keep reading/writing the
  legacy `platform: "gm"` value, treating it as `(hptuners, gm, gm_gen3_4_ls)`.
- **Phase 2:** factor `engine_gm.py` into a `gm_gen3_4_ls` architecture module
  behind a strategy interface; `detect_platform()` returns
  `(platform, make, architecture)` with a back-compat shim.
- **Phase 3:** land Ford Coyote as the first non-GM architecture (proves the
  seam), with Ford table names under the HP Tuners platform.

Back-compat rule: a garage saved today (`platform: "gm"`) must keep loading
forever; the loader maps legacy values onto the new axes.
