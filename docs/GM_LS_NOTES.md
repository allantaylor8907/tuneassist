# GM LS tuning notes (HP Tuners) — detail behind the detectors

Distilled from the LS Toolbox guides (babendums) and used to ground our detectors
and prescriptions. Pairs with docs/TUNING_BY_PLATFORM.md (the high-level matrix).
Gen 3 = P01 (1999-2002) / P59 (2003-2007): a real RPM×MAP VE table + a MAF
frequency→airflow table. Gen 4 = Virtual VE (not this workflow).

## Gen 3 MAF + VE workflow (the canonical order we encode)

Goal: make the MAF and VE airflow models *agree*. Isolate one, tune it, isolate
the other, tune it, restore the blend, verify the handoff.

1. **Foundation first** (don't let the airflow tables absorb others' errors):
   correct injector data (flow/offset/short-pulse/min-PW), known fuel pressure,
   correct stoich, no intake/exhaust leaks, healthy ECT/IAT/MAP/TPS/MAF/wideband,
   no misfire. **A wideband is required for PE/WOT** (narrowbands only switch at
   stoich).
2. **Tune VE in speed-density** by *forcing a MAF failure* — set MAF-fail-high-
   frequency to 0 (or lowest) and enable P0101/P0102/P0103, flash, confirm a MAF
   DTC, verify the PCM is on VE. (Don't just unplug the MAF — some tunes disable
   the DTC and won't fall back.) Correct the RPM×MAP table multiplicatively to
   **±2-3%** in the cells the car actually drives.
3. **Tune the MAF curve**: restore MAF, then minimize dynamic airflow so MAF is
   the active model everywhere above idle — a common temporary setup is **Dynamic
   Airflow High-RPM Disable ≈ 400 / Re-enable ≈ 300**. The MAF table is 1-D
   (frequency→airflow); correct by %, then **smooth monotonic** (airflow must
   increase with frequency).
4. **Restore** MAF-fail + P010x + dynamic-airflow to stock, clear DTCs, then log
   normal mixed driving and **validate the handoff**: trims shouldn't jump as the
   PCM crosses the dynamic-airflow threshold; PE entry should hit commanded
   lambda; knock clean.

Correction math: `new = old × (1 + error%/100)`. Apply **50-75% of large
corrections** first; require **≥10-20 hits** per cell; never "hero" a single cell.
Filter out cold, PE, DFCO, rapid throttle/MAP/RPM, misfire/knock, low-hit cells.

**Key tell we already encode:** if the *same %* error shows almost everywhere
(especially after an injector swap) it's **injector data / fuel pressure, not VE**
— that's our `offset_vs_shape` global-offset detector + the "larger injectors"
mod insight. PE: leaner-than-commanded → raise the airflow model, don't paper
over it with the PE table.

## Gen 3 idle & startup (symptom → direction)

Mental model: **airflow holds the engine up; spark catches fast RPM error; fuel
makes the mixture correct — don't make one system fix another's problem.** Tune
idle only after MAF/VE are close; a 12%-lean idle cell is an airflow bug, not an
idle-table bug.

Tables: Desired Idle RPM, **Base Running Airflow** (corrected via the logged
**RAFIG** in-gear / **RAFPN** park-neutral airflow corrections, indexed by ECT),
Idle Spark, IAC (DBC) / throttle area (DBW), Throttle Cracker, Throttle Follower,
Cranking Fuel, Startup Airflow, Afterstart Enrichment.

| Symptom | Likely direction |
|---|---|
| Flares high / hangs after start | too much startup airflow or slow decay |
| Starts then dies unless you blip it | too little startup airflow / base airflow too low |
| Long crank, won't catch | cranking fuel (lean) or sync |
| Rich stumble after start, clears | too much afterstart fuel |
| Stalls on coastdown / clutch-in | too little throttle cracker |
| Rolling idle hangs high | too much throttle cracker |
| RPM dips/hangs after a blip | throttle follower too little / too much |
| Idle hunts, spark sawing | base airflow wrong; **don't fix airflow with spark** |
| Big RAFIG/RAFPN correction | base running airflow off at that ECT |
| Cammed idle unhappy | usually **airflow**, not more timing; raise target RPM in 25-50 steps |

Pitfalls: tuning idle before MAF/VE; using Desired Idle RPM as a bandage;
closing the throttle so far the IAC has no authority (IAC pinned at 0 or max =
warning); chasing fuel when a start-and-die is really an *air* problem (misfire
fakes a lean wideband); killing idle spark correction on a street tune.

## Gen 4 automatic transmission (4L60/65/70E, 4L80/85E, 6L80/90)

Channels: current gear vs **commanded gear**, VSS, input/output shaft speed,
**torque reduction/management** (during the shift), line pressure / force motor /
PCS, **TCC slip & lock state**, trans temp. Judge shifts **hot**, not cold.

- **WOT shift**: the *actual* shift RPM is what matters — if a 1-2 finishes ~300
  RPM above the command, set the command lower to land where you want. (We read
  the pre-shift RPM via `rpm.shift(1)` for exactly this.)
- **TCC**: no lockup during unstable low-speed throttle; smooth apply at light
  cruise; some tunes command controlled slip.
- **Line pressure / force motor**: support the shift, don't use pressure to mask a
  bad shift schedule.
- **Torque management**: it's part of the shift event — **don't disable it
  globally on 6L80/6L90** or you get flare. One change per relog, every change
  before/after logged.

## Sources
LS Toolbox (babendums): Gen 3 MAF & VE tuning, Gen 3 idle & startup tuning, Gen 4
LS automatic transmission tuning, and the Gen 3-vs-Gen 4 primer —
https://lstoolbox.babendums.com/ . Corroborates The Tuning School + HP Tuners
forum sources in docs/TUNING_BY_PLATFORM.md.
