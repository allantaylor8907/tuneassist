# DESIGN.md — tuning logic and the reasoning behind it

This is the domain knowledge. The code is recoverable; this reasoning is the
expensive part. Read before changing correction math.

## 1. Recommendation-only, and why
No vendor exposes a public API to read/write tune files, and the formats are
closed. Reverse-engineering them is fragile and ToS-gray. More importantly,
auto-applying fuel changes to an engine without a human in the loop is dangerous.
So the tool outputs a change-set; the human applies it in VCM Editor / Holley EFI.
This is the correct engineering posture, not a limitation to remove.

## 2. GM / HPTuners correction logic (speed-density)
Per operating cell (RPM × MAP, MAP in kPa):

- **Closed loop (cruise/light load):** the PCM is already trimming to stoich, so
  the VE error *is* the trim it applied.
      correction = 1 + (STFT + LTFT)/100      (banks averaged if both logged)
  Positive trim ⇒ PCM added fuel ⇒ VE under-estimated air ⇒ raise VE.
- **Open loop (WOT / power enrichment):** no trims; use the wideband.
      correction = measured_AFR / commanded_AFR

Closed-loop detection, best signal first: explicit status channel → commanded
AFR ≥ 14.0 (stoich) → commanded lambda ≤ 1.03 → TPS < 35% fallback. Power
enrichment commands a rich target, which is how open loop is detected without a
status channel.

## 3. The ECM-blind wideband (Allan's AEM on the GM truck) — IMPORTANT
The AEM wideband feeds the laptop's serial port into VCM Scanner as an external
channel. **The ECM never sees it.** Consequences:
- At WOT (open loop) the wideband is the *only* valid correction source, because
  the ECM runs open-loop off its airflow model with no AFR feedback at all.
- At cruise (closed loop) the wideband is a *cross-check*, not the correction
  source: the ECM controls to stoich via its narrowband, so the wideband should
  read ~stoich. We use the trims for the correction there, never the wideband.

## 4. Cross-check classifier (which table to touch)
In closed-loop cells where a wideband is present, classify each confident cell:
- **wideband disagrees with commanded (> `o2_suspect` %)** → narrowband O2 bias
  or fuel-content/stoich mismatch. The trims are chasing a lying sensor. → flag
  **O2/STOICH**, do NOT recommend an airflow change here until that's resolved.
- **wideband confirms stoich AND |trim| > `trim_conf`** → real airflow error →
  **VE/MAF** change (apply the multiplier).
- else → **OK**, leave alone.
Open-loop cells → **WOT** (wideband-vs-commanded airflow correction).
(Implemented in `engine_gm._classify_cells`; finish wiring into `analyze`.)

## 5. MAF-vs-VE attribution (Gen 3/4 run blended)
These trucks blend MAF and VE airflow, MAF-dominant at cruise. If MAF and VE
airflow channels track each other closely (they did on the sample Silverado:
medians 5.74 vs 5.79 lb/min), a persistent trim is more likely a MAF-curve or a
global scalar issue (injector flow, fuel pressure) than a VE *table-shape* issue.
A roughly **flat** correction across the whole map ⇒ global offset; a correction
that **varies with RPM/MAP** ⇒ table shape. Recommend the global scalar / MAF
first when the correction is flat.

## 6. Holley correction logic (different paradigm)
Holley's wideband is the control sensor; the ECU writes corrections into Target
AFR vs actual via Closed-Loop Compensation and a persistent Learn table. So:
- Method A: correction = Target_AFR / Actual_AFR (direct).
- Method B: correction = 1 + (CL_Comp + Learn)/100 (what the ECU already learned).
Prefer B where Learn has converged; use A elsewhere; flag cells where A and B
disagree (Learn hasn't reached that cell). See `holley.analyze_holley`.

## 7. Triage (preflight gate)
Run before correction, always. States and the next-step advice each gives:
NO_CRANK (starter/battery/crank-sensor), CRANKING_NO_START (sync/spark/fuel),
STARTED_STALLED (idle air/base fuel/idle spark), UNSTABLE_IDLE (idle air/vac
leak/idle spark), IDLE_ONLY (drive it to populate the map), RUNNING_DRIVE
(proceed). Thresholds in `triage.TriageThresholds`. LS and Holley both crank
~120-300 rpm and "run" north of ~550.

## 8. Filtering before correction
Warm only (coolant ≥ 160 °F — a cold clip is correctly rejected outright),
drop hard transients (TPS rate), drop idle-off/cranking (RPM ≤ 400), trimmed
mean per cell to kill outliers/glitches.

## Known fixtures and expected behavior (build these into tests/)
- Silverado cruise CSV: flat ~−4.7% rich bias, ~62% coverage. Global-offset shape.
- `ride42` CSV (AEM wideband): median ~−0.8%, scattered ±3-6%, well-sorted tune.
- `jr42` CSV: REJECTED — engine never warmed (max coolant 111 °F).
- `protuner12` CSV: REJECTED — spark/diag log, no RPM/fuel channels.

## 9. The GM Gen 3/4 airflow model, and the VE-then-MAF order of operations
A Gen 3/4 LS PCM estimates cylinder airmass two ways and blends them:
- **MAF** — the mass-airflow sensor frequency → g/s via the MAF calibration
  table. Trusted as the *primary* airflow source at steady state when present
  and in range.
- **Speed-density (SD)** — `airmass = f(VE_table[RPM, MAP], MAP, IAT)` via the
  ideal-gas law. Used at idle / very low airflow, during fast transients (MAF
  lags), and as the failsafe when the MAF is disabled or DTCs.

Because both feed the *same* fuel calc, a fuel-trim error with MAF enabled is
**ambiguous** — you can't tell whether the VE table or the MAF curve is wrong.
So the community-standard method (tuning101, Goat Rope Garage, HPTuners forums)
separates them, and this tool guides that order explicitly:

1. **Tune VE first with the MAF disabled** (force SD-only). With the MAF out of
   the loop the PCM fuels purely off the VE table, so STFT+LTFT *are* the VE
   error. Drive cruise + light load, correct the VE table (our existing closed-
   loop correction), re-log until trims are flat (≈ ±3 %). This is the
   `TUNE_VE_SD` journey phase.
2. **Re-enable the MAF and tune the MAF curve** with VE now correct. The SD
   airmass is trustworthy, so it becomes the *reference*: MAF correction =
   SD_airmass / MAF_airmass (or, equivalently, the residual trims with MAF on).
   The MAF table is indexed by **frequency (Hz)**, not RPM×MAP — so when MAF
   frequency is logged we bin the error by frequency and emit a 1-D Hz→% table.
   MAF is a steady-state sensor: only use cruise/steady samples, never
   transients. This is the `TUNE_MAF` journey phase.
3. With both correct the blend just works; trims stay tight everywhere.

Detection / guidance: the wizard asks the airflow strategy (MAF disabled→VE,
MAF enabled→normal/blended, no-MAF SD-only, or "tuning MAF now"). In VE-SD mode
corrections route to the **VE table**; in MAF mode they route to the **MAF
curve** (frequency table if available). A "no MAF at all" SD-only setup (common
on cammed/forced-induction builds) just stays in VE mode permanently. The
flat-vs-shape detector (§5) still applies inside VE mode: a flat error is a
global scalar (injector flow / fuel pressure / SD multiplier), not table shape.

## 10. Spark / timing for power — knock-governed, safety-first
Goal is **MBT** (Minimum spark for Best Torque): advance timing until torque
stops climbing or knock appears, then back off for margin. Too little timing
leaves power on the table and raises EGT; too much is detonation and broken
ring-lands. So spark work here is **knock-governed and conservative**, and the
tool will not invent advance blind.

Hard prerequisite: a **knock-retard channel must be logged** before any spark
recommendation. No knock feedback ⇒ spark analysis is refused (guidance only).

Per RPM×MAP cell, from a WOT/loaded log:
- **Knock present** (retard > `knock_pull_deg`): the cell has too much advance
  *or* a root cause (lean AFR, high IAT, bad fuel). Recommend pulling that cell
  by `observed_retard + knock_pull_margin` and flag the likely root cause if AFR
  is lean or IAT high there. This is the **high-confidence** recommendation.
- **No knock, headroom exists, user opted into "find power"**: suggest a small
  `spark_add_step` (default +1°, capped at `spark_add_max` per pass) in the
  torque-relevant cells, *only* where IAT ≤ `iat_spark_safe` and AFR is at/rich
  of target. Re-log and repeat; stop when torque flattens or knock shows, then
  back off `spark_back_off`. Increments are deliberately tiny — you sneak up on
  MBT across passes, you don't jump.
- **Heat / IAT**: high IAT (heat-soak, forced induction) is a knock multiplier.
  Flag cells where IAT is high and ensure the IAT spark-retard compensation is
  active. Rough guide surfaced as advice, not auto-applied: ~1° per 10 °F above
  ~100 °F IAT, engine-dependent.

Sane WOT totals we surface as an *advisory ceiling*, never an auto-target, and
we tailor it from the optional **engine profile** (`profile.py`): block material,
static compression, power adder. The split matters — a low-compression **iron**
5.3 tolerates more static timing but holds heat (knock creeps in on back-to-back
pulls), while a higher-compression **aluminum** 5.7 sheds heat but detonates
sooner, so its limit is compression/fuel-bound. Rough bands: NA pump ~24–28°
(higher CR → ~22–25°, low CR → ~25–29°), E85 +~2°, boost ~10–18° (less with more
boost). The profile also drives a build-specific "pull timing back when…"
checklist (knock, IAT/heat-soak, hot coolant, lean-under-load, lower octane,
raising boost). Holley spark tables differ in structure but the same MBT/knock
logic applies.

## 11. Cam specs → starting points (optional input)
A bigger cam (more duration @ .050, wider overlap, tighter LSA) lowers dynamic
compression and adds intake dilution/reversion at low RPM and idle. Consequences
the tool uses for *starting points only* (always knock-verified after):
- **Idle timing** wants to be higher with a big cam (dilution needs more
  advance to burn) — stock ~14–18°, a healthy cammed LS often likes ~20–28° at
  idle. We nudge an idle-timing starting suggestion from duration/LSA.
- **Low-RPM / light-load knock tolerance** improves (lower cylinder pressure),
  so a bit more part-throttle advance is usually safe — but still verify.
- Torque peak shifts up; expect emptier low-RPM VE cells and a higher useful
  RPM range when prescribing drives.
We ask for duration @ .050 (int/exh), LSA, and optionally lift; all optional. We
classify the cam (stock/mild/big) and emit conservative starting numbers with a
loud "verify with a knock-logged pull" caveat. We never target a cam-derived
number without knock confirmation.

## Open ideas
- Multi-pass convergence tracking persisted to disk (survives across runs).
- LT (Gen 5, DI) airflow + Holley LT later — different airflow model, deferred.
- EGT / fuel-pressure cross-checks for the lean-vs-airflow root-cause split.
