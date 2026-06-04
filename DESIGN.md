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

## 13. What the mods do — using bolt-ons to explain the data (`core._apply_mod_insights`)
The user picks an engine preset and checks mods. Those mods predict *how the data
should look*, so we use them to explain findings and sharpen advice (not to invent
changes). Each insight only fires when the data actually shows the matching
pattern — so it's context, not noise.

- **Larger injectors** → a global fueling scalar. Bigger injectors flow more, so
  if the injector flow-rate/scaling isn't updated the engine runs rich/lean by a
  roughly **flat** amount everywhere. So: larger-injectors + a *flat global
  offset* (§5) ⇒ it's almost certainly the **injector data**, not the VE table —
  fix the flow rate first, it flattens most of the correction. (Big injectors
  also idle poorly at tiny pulsewidths — a separate nonlinearity.)
- **Ported heads / long-tube headers / cold-air intake / bigger throttle body**
  → more airflow, weighted to higher RPM/load. The engine breathes *more than the
  stock VE table models*, so a **raise-VE up top** is *expected calibration*, not
  a fault. We say so when the correction is positive in the upper RPM/load cells.
- **Long-tube headers** also → a **collector/gasket exhaust leak near one O2** is
  common right after install and reads *false-lean* on that bank → added as a
  prime cause when a **bank imbalance** shows up.
- **Cold-air intake / intake tube change** on a MAF setup → changes the MAF's
  airflow signal (tube diameter/velocity), so **lean cruise** trims may be a
  **MAF-curve** recal, not VE. Also lowers IAT (good for knock).
- **Intake-manifold swap** → different runner length/plenum **reshapes the VE
  curve** (moves where it breathes best) ⇒ expect *table-shape* changes, not a
  single scalar. Said when the correction varies (table-shape).
- **Aftermarket cam** (also see §11) → lower idle vacuum (higher, unsteadier idle
  MAP), overlap dilution/reversion at idle (can mimic a vacuum leak → we already
  soften leak confidence), torque peak higher, VE shifts up in RPM.
- **Turbo / Supercharger / Nitrous** → set the power-adder; drive the
  forced-induction / nitrous logic (§12, §10). Checking them in setup flips the
  profile's `power_adder` automatically.

## Known fixtures and expected behavior (build these into tests/)
- Silverado cruise CSV: flat ~−4.7% rich bias, ~62% coverage. Global-offset shape.
- `ride42` CSV (AEM wideband): median ~−0.8%, scattered ±3-6%, well-sorted tune.
- `jr42` CSV: REJECTED — engine never warmed (max coolant 111 °F).
- `protuner12` CSV: REJECTED — spark/diag log, no RPM/fuel channels.
- `holley_sample` CSV (Terminator X): RUNNING_DRIVE; Learn-based base-fuel
  correction ~−18% (ECU had learned ~−20%); WOT actual≈commanded 13.2 → a power
  *opportunity* (richen toward 12.8), NOT a false lean; no narrowband finding.
- `sniper_sample` CSV (Sniper V2): RUNNING_DRIVE; small base-fuel correction
  (well-tuned). No knock channel (Sniper has none). `Fuel Press Switch` must NOT
  resolve as fuel pressure. No false IDLE_HUNT (decel/coast excluded via the
  MAP+speed idle mask).

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

## 12. Diagnostic catalog — symptom → cause → correction (`diagnostics.py`)
The correction math (VE/MAF/spark) answers "how far off is each cell." This
catalog answers the *diagnosis* question a good tuner asks: given the **pattern**
of what the log shows, what's the likely **root cause**, and what do you actually
**change** — including where there's free power. Each detector degrades
gracefully (skips itself if its channels aren't logged) and reports a confidence.

Severity: `critical` (engine at risk — surface first), `warning` (drivability /
tune quality), `opportunity` (free power / refinement), `info`.

### Fuel & trims
- **Lean cruise** (mean STFT+LTFT > +`lean_trim`%, broadly): airflow model
  under-reads. Causes, ranked: VE/MAF too low (→ apply the correction grid);
  **vacuum leak** (suspect when idle/low-load trim ≫ cruise trim — unmetered air);
  injector data / dead-time low; failing fuel pump if it worsens with load;
  MAF placement/leak after the sensor.
- **Rich cruise** (mean trim < −`rich_trim`%): airflow over-reads. Causes:
  VE/MAF too high; leaking/oversized injector; fuel pressure high; contaminated
  MAF reading high; wrong injector scaling.
- **Bank imbalance** (|trim_b1 − trim_b2| > `bank_split`%): one-bank problem —
  injector(s) on that bank, a single bad O2, an exhaust leak upstream of one
  sensor (false-lean → adds fuel), or an intake/vacuum leak feeding one bank.
- **Vacuum leak** (common on swaps): a leak is a *fixed* amount of unmetered air,
  so its fueling effect is a large % at idle (low airflow) and **tapers as airflow
  rises**. The detector flags a high idle fuel-add (STFT+LTFT on GM, CL-comp+Learn
  on Holley) that exceeds the cruise add by `vac_idle_delta`. Corroboration raises
  confidence: a **lean idle AFR** (leak past trim authority) → high; a known **big
  cam** can mimic the idle signature (reversion/dilution) → confidence dropped to
  low with a "smoke-test to confirm" note. Fix the leak (intake/TB gasket, PCV,
  booster hose, injector o-rings) BEFORE tuning idle — never "tune out" a leak.
- **Trim oscillation** (high STFT variance): O2 wiring/heater, exhaust leak near
  sensor, or closed-loop gains too aggressive.
- **Trims clipping** (a bank pegged near the ~±25 % authority): the ECU is out of
  room to correct — the base table/airflow is way off, or there's a big leak /
  fuel-supply problem. (Well-tuned LS LTFT sits within ~±4 %.)
- **Low system voltage** (avg < ~12.8 V running): slows the fuel pump and
  lengthens injector opening → fueling drifts; check charging + injector dead-time
  (offset-vs-voltage).
- **Low fuel pressure** (base < ~38 psi for port EFI): less fuel per pulse, lean
  tendency worst up top → set base pressure to spec before tuning fuel.

### O2 / wideband
- **Wideband disagrees with commanded in closed loop** (>`o2_suspect`%, see §4):
  narrowband bias, exhaust leak at the NB sensor, or **fuel-content/stoich
  mismatch** (running E-blend on a gasoline stoich, or vice-versa). The ECM
  trims to a lying sensor → resolve the sensor/stoich BEFORE chasing airflow.
- **Wideband pegged** (stuck ~max lean/rich): sensor fault or free-air calibration
  drift — don't tune off it until verified.

### MAF-specific
- **MAF vs SD divergence**: with VE correct, MAF airmass should track SD airmass.
  Persistent gap → MAF curve wrong, contamination (oiled filter → reads low →
  false lean), a leak between MAF and throttle, or bad sensor placement/tubing.
- **MAF spikes/dropouts**: reversion (big cam, no screen), wiring, or sensor.

### Spark / knock  (detail in §10, summarized here as findings)
- **Knock under load**: too much advance, lean AFR, high IAT, low octane, or
  carbon/hot-spot. Pull timing there (+margin); fix lean/IAT root cause first.
- **Knock only when heat-soaked**: cooling + IAT-based spark retard; iron blocks
  especially (§11). **Cruise/light-load knock**: over-aggressive economy timing.

### Cold start & warmup (`_coldstart_findings`, runs on the FULL log)
- **Never reached operating temp**: over a long log (`warmup_min_duration_s`)
  coolant stays below `thermostat_min_f` → stuck-open/missing/wrong thermostat;
  also means any cruise/VE data while cold isn't valid to tune on.
- **Warmup rich / lean**: while warming (ECT between ~90 °F and operating temp),
  wideband AFR richer than `warmup_rich_afr` (loads up / fouls / washes bores) or
  leaner than `warmup_lean_afr` (cold stumble/stall) → trim the coolant/afterstart
  enrichment-vs-temp curve.
- **Enrichment not decayed**: afterstart/coolant enrichment still elevated when
  warm. We detect the channel's *neutral baseline* (0 = none, or 100 % = neutral
  on Holley) so a settled 100 % is NOT flagged — only a real elevation above
  neutral is. → taper enrichment to neutral by operating temp.

### Idle quality (`_idle_findings`, warm idle samples)
- **Hunting / surging**: idle RPM std above `idle_hunt_std` → vacuum leak (lean
  hunt), IAC range/min-air, idle spark correction too strong, or loading up rich.
- **Off-target idle**: actual idle RPM vs the logged target idle beyond
  `idle_rpm_tol` — high (leak / throttle stop / IAC can't pull it down) or low
  (not enough idle air, IAC out of authority, idle timing too low / stall risk).
  When no target is logged we infer a typical idle from the cam class (HP Academy /
  HP Tuners: stock LS ~550-600, mild ~760, big-cam ~850; wider tolerance, info
  severity). Idle is read on a true-idle mask (`_idle_mask`): low RPM, idle-range
  MAP, and vehicle speed ~0 — so closed-throttle decel/coast doesn't look like a hunt.
- **Idle AFR** rich (`idle_afr_rich`) or lean (`idle_afr_lean`).
- **IAC fully closed** at idle yet idle holds → extra unmetered air (leak) or the
  throttle stop is cracked too far (IAC has no room to control).
- **Idle timing swinging** (spark std > `idle_timing_std`) → idle spark-vs-RPM
  correction amplifying the hunt; soften it while stabilizing airflow/fuel.

### Drivability (transient — future, see Open ideas)
- **Tip-in stumble**: accel-enrichment too low or MAF transient lag (SD fill).
- **Decel popping**: too much decel fuel or an exhaust leak with lean decel.
- **Steady-cruise surge**: cruise too lean, torque management, or CL oscillation.

### Cooling / charge temp
- **Overheat** (ECT > `ect_hot`): cooling/fan tables, or a lean/over-advanced
  tune making heat. **High IAT** (> `iat_hot`): heat-soak/intake — knock risk,
  density loss; ensure IAT spark comp is active.

### Fuel-system limits
- **Injector duty maxed** (duty > `inj_duty_max`%, duty ≈ PW_ms·RPM/1200):
  injectors undersized or fuel pressure dropping → out of fuel up top. Bigger
  injectors / pump / regulator before leaning anything at WOT.

### Forced induction (turbo / supercharger)
Boost is detected from MAP exceeding baro (so these only fire on a boosted log,
or when the engine profile says boost). Boost cells are **open-loop and
unforgiving** — fuel and timing margin matter most here.
- **Lean under boost** (AFR > `boost_lean_afr`, ~11.9): the fastest way to melt a
  piston. Boosted NA-block engines want ~11.0–11.8. Causes: fuel system out of
  headroom (injectors/pump/pressure), boost AFR target too lean, fuel pressure
  not rising with boost. → richen the boost target and confirm the fuel system
  can deliver before adding boost.
- **Fuel pressure dropping under load**: base fuel pressure should hold, or rise
  1:1 with boost (boost-referenced regulator). A drop from idle→boost means the
  pump/lines/regulator can't keep up — and a pressure drop *causes* the lean-out.
- **MAP sensor can't read boost**: profile says boost but MAP never clears ~1 bar
  — a stock 1-bar sensor is blind above atmospheric. → fit/scale a 2- or 3-bar
  sensor so the tune can see boost at all.
- **Closed-loop fueling under boost**: trims still active under boost means PE /
  open-loop hands off too late; CL can lean a rich PE target back out. → drop out
  of closed loop before boost.
- **Hot charge under boost** (IAT > `boost_iat_hot`): weak/heat-soaked intercooler
  — steals power and invites knock. → improve charge cooling; lean on IAT retard.
Spark under boost is far more conservative than NA (§10: ~10–18°, less with more
boost; pull timing as boost/IAT rise).

### Squeezing power (opportunity findings)
- **WOT richer than needed**: measured WOT AFR well rich of a safe NA target
  (~12.5–12.8 pump, ~11.8–12.2 boost) leaves power on the table → lean the PE
  target toward optimal **cautiously, knock-watched**. Too-rich also washes oil.
- **Timing headroom**: WOT, knock ≈ 0, timing below the §10 advisory band → room
  to add small increments toward MBT (defer to the spark "find power" path).
- **Torque management** (GM): factory pulls timing/throttle in gear; if a
  torque-management channel shows reduction without knock, easing it recovers
  power (advanced users; note, don't auto-apply).
- **PE enable too conservative**: power enrichment that comes in late/high-TPS
  loses transitional power; commanded-AFR enrichment point can be brought in.

Thresholds live in `DiagnosticConfig`. Findings are advisory and ranked; the
critical/safety ones (WOT lean, injector maxed, overheat, knock) sort to the top.

## Open ideas
- Multi-pass convergence tracking persisted to disk (survives across runs).
- LT (Gen 5, DI) airflow + Holley LT later — different airflow model, deferred.
- EGT / fuel-pressure cross-checks for the lean-vs-airflow root-cause split.
- Drivability detectors that need transient analysis (tip-in stumble, decel pop).
