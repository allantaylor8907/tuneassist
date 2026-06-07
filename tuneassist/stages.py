"""
stages.py -- the tuning *journey* state machine.

cli.py answers "what's wrong with this one log?". This module answers the bigger,
logistical question the user actually lives in: "given where this build is right
now, what is the single next thing I should change, what drive should I go do,
and what should I log on that drive?" -- then it does it again on the next log,
walking a not-yet-running engine all the way to a converged tune.

It is pure logic on top of a triage result + an analysis Result, so it is fully
testable without a terminal. The wizard (wizard.py) renders what this decides.

Journey ladder (a build climbs it across successive logs):

  GET_RUNNING        engine won't crank/start/stay running  -> get it alive
  STABILIZE_IDLE     runs but idle hunts                     -> steady the idle
  DIAL_IDLE_CRUISE   stable idle, no driving data yet        -> go drive it
  CORRECT_VE         driving data; cruise VE still off       -> apply VE, re-log
  TUNE_POWER         cruise converged; WOT cells unexplored  -> safe WOT pulls
  CONVERGED          corrections tiny everywhere             -> done / maintenance
"""

from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .engine_gm import offset_vs_shape


# Journey stages, ordered. (key, human title). Mirrors DESIGN.md S9/S10:
# get it alive -> steady idle -> collect driving data -> VE (MAF off) ->
# MAF curve -> WOT fuel -> spark -> done.
STAGES = [
    ("GET_RUNNING",      "Get it running"),
    ("STABILIZE_IDLE",   "Stabilize the idle"),
    ("DIAL_IDLE_CRUISE", "Get driving data"),
    ("TUNE_VE_SD",       "Tune VE (MAF off)"),
    ("TUNE_MAF",         "Tune MAF curve"),
    ("TUNE_POWER",       "Tune WOT fuel"),
    ("TUNE_SPARK",       "Tune spark"),
    ("CONVERGED",        "Converged"),
]
STAGE_TITLE = dict(STAGES)
STAGE_ORDER = {k: i for i, (k, _) in enumerate(STAGES)}

# Load split (MAP kPa). Below LOW = cruise/light; at/above HIGH = WOT/power.
CRUISE_MAP_MAX = 70.0
WOT_MAP_MIN = 80.0

# In MAF mode, a systemic offset this big (|median|%) across the map is VE-table
# error, not MAF-curve error -- so we route the user back to the VE pass instead
# of mislabeling a big whole-map shift as "tune the MAF curve".
MAF_SYSTEMIC_PCT = 3.5


@dataclass
class AnalysisSummary:
    """A digest of an engine_gm/holley analysis Result, in the terms the journey
    state machine reasons about."""
    coverage_pct: float = 0.0
    n_confident: int = 0
    n_total_cells: int = 0
    median_pct: float = 0.0
    max_abs_pct: float = 0.0
    cruise_max_abs_pct: float = 0.0   # |change| over confident cruise cells
    wot_covered: bool = False         # any confident WOT cells?
    wot_max_abs_pct: float = 0.0
    offset: dict = field(default_factory=dict)   # offset_vs_shape() output
    o2_suspect_cells: int = 0         # cells where wideband disputes narrowband
    has_wideband: bool = False
    safety_types: list = field(default_factory=list)
    focus: str = ""                   # short "where the work is" phrase

    @property
    def cruise_converged(self) -> bool:
        # cruise is done when its confident cells barely move
        return self.n_confident > 0 and self.cruise_max_abs_pct < 2.0

    @property
    def converged(self) -> bool:
        return self.n_confident >= 6 and self.max_abs_pct < 1.5


def summarize(result, cfg) -> AnalysisSummary:
    """Build an AnalysisSummary from an engine_gm.Result (works for the Holley
    grid too -- it only needs .correction, and the rest degrade gracefully)."""
    s = AnalysisSummary()
    corr = getattr(result, "correction", None)
    if corr is None or corr.empty:
        return s

    conf = corr.notna()
    s.n_total_cells = int(conf.size)
    s.n_confident = int(conf.values.sum())
    s.coverage_pct = 100.0 * s.n_confident / s.n_total_cells if s.n_total_cells else 0.0

    pct = (corr.stack().dropna() - 1.0) * 100.0
    if len(pct):
        s.median_pct = round(float(pct.median()), 2)
        s.max_abs_pct = round(float(pct.abs().max()), 2)

    # Split confident cells by load (columns are MAP-kPa bins as Intervals).
    cruise_vals, wot_vals = [], []
    for r in corr.index:
        for c in corr.columns:
            v = corr.loc[r, c]
            if pd.isna(v):
                continue
            mid = getattr(c, "mid", None)
            load = float(mid) if mid is not None else float("nan")
            change = abs(v - 1.0) * 100.0
            if load <= CRUISE_MAP_MAX:
                cruise_vals.append(change)
            elif load >= WOT_MAP_MIN:
                wot_vals.append(change)
    if cruise_vals:
        s.cruise_max_abs_pct = round(max(cruise_vals), 2)
    if wot_vals:
        s.wot_covered = True
        s.wot_max_abs_pct = round(max(wot_vals), 2)

    s.offset = offset_vs_shape(corr, cfg)

    rec = getattr(result, "recommendation", None)
    if rec is not None and not rec.empty:
        flat = rec.stack().dropna()
        s.o2_suspect_cells = int((flat == "O2/STOICH").sum())
    s.has_wideband = getattr(result, "wb_dev", None) is not None
    s.safety_types = [e.get("type") for e in getattr(result, "safety", [])]

    # Where is the work?
    if s.cruise_max_abs_pct >= s.wot_max_abs_pct and s.cruise_max_abs_pct > 0:
        s.focus = f"cruise/light-load (up to {int(s.cruise_max_abs_pct)}% off)"
    elif s.wot_max_abs_pct > 0:
        s.focus = f"high-load/WOT (up to {int(s.wot_max_abs_pct)}% off)"
    return s


@dataclass
class Prescription:
    """The single recommended next move in the journey."""
    stage: str
    title: str
    rationale: str
    actions: list          # what to change in the vendor software now
    drive: str             # the drive to go capture next
    capture: list          # channels / conditions to log on that drive
    converged: bool = False


# Channel wish-lists for the "log these next" guidance.
_GM_CHANNELS = [
    "Engine RPM", "MAP (or convert psi->kPa)", "TPS",
    "STFT + LTFT (both banks)", "Commanded AFR", "Coolant temp", "IAT",
    "Knock retard", "(wideband AFR if you have one)",
]
_GM_START_CHANNELS = [
    "Engine RPM", "Cam/Crank sync status", "Injector pulsewidth or duty",
    "Spark/ignition", "Battery voltage", "Coolant temp",
]


def determine_stage(triage_state: str, summary: AnalysisSummary,
                    airflow_mode: str = "ve_sd", tune_spark: bool = False,
                    spark_has_work: bool = False) -> str:
    """Map (triage state, analysis digest, user intent) -> journey stage key.

    airflow_mode: 've_sd' (MAF disabled, tuning VE), 'maf' (tuning the MAF curve),
    or 'no_maf' (SD-only setup, MAF stage skipped entirely)."""
    not_running = {
        "NO_DATA": "GET_RUNNING",
        "NO_CRANK": "GET_RUNNING",
        "CRANKING_NO_START": "GET_RUNNING",
        "STARTED_STALLED": "GET_RUNNING",
        "UNSTABLE_IDLE": "STABILIZE_IDLE",
        "IDLE_ONLY": "DIAL_IDLE_CRUISE",
    }
    if triage_state in not_running:
        return not_running[triage_state]

    # RUNNING_DRIVE: the analysis + intent decide how far along we are.
    if summary.n_confident == 0:
        return "DIAL_IDLE_CRUISE"

    # 1) airflow not converged yet -> stay in the current airflow phase. BUT a
    # systemic whole-map offset in MAF mode is VE-table error (the MAF curve only
    # makes sense once VE is right), so route back to the VE pass rather than
    # calling a big shift "tune the MAF curve".
    if not summary.cruise_converged:
        if airflow_mode == "maf" and abs(summary.median_pct) >= MAF_SYSTEMIC_PCT:
            return "TUNE_VE_SD"
        return "TUNE_MAF" if airflow_mode == "maf" else "TUNE_VE_SD"

    # 2) VE (SD) converged -> next phase is the MAF curve (unless there's no MAF)
    if airflow_mode == "ve_sd":
        return "TUNE_MAF"

    # 3) MAF / no-MAF: cruise done -> WOT fuel if it still needs work
    if not summary.wot_covered or summary.wot_max_abs_pct >= 2.0:
        return "TUNE_POWER"

    # 4) fuel fully done -> spark (if requested and there's something to do)
    if tune_spark and spark_has_work:
        return "TUNE_SPARK"
    return "CONVERGED"


def prescribe(stage: str, summary: AnalysisSummary, triage_recs: list,
              platform: str = "gm", airflow_mode: str = "ve_sd",
              cam_points=None, spark=None, architecture: str = "gm_gen3_ls") -> Prescription:
    """Turn a stage into a concrete next-move prescription. Non-running stages
    lean on triage's targeted recommendations; running stages reason off the
    analysis digest, the airflow mode, optional cam starting points, the optional
    spark result, and the engine architecture (Gen 3 vs Gen 4 tune differently --
    see docs/TUNING_BY_PLATFORM.md)."""
    gm = platform != "holley"
    gen4 = architecture == "gm_gen4_ls"
    gen5 = architecture == "gm_gen5_lt"
    cam_notes = list(cam_points.notes) if cam_points else []

    if stage == "GET_RUNNING":
        return Prescription(
            stage, STAGE_TITLE[stage],
            "The engine isn't in a running state yet, so there's no fuel/VE error "
            "to read -- first job is simply to make it run.",
            actions=triage_recs or ["Work through crank -> sync -> spark -> fuel."],
            drive="Don't drive. Make a start attempt and capture the crank/start.",
            capture=_GM_START_CHANNELS if gm else
                    ["RPM", "Sync", "Injector PW", "Ignition", "Battery", "Coolant"],
        )

    if stage == "STABILIZE_IDLE":
        actions = triage_recs or [
            "Set a sane idle airflow target / IAC range.",
            "Hunt for vacuum leaks (lean hunt) and over-rich idle (loads up).",
            "Soften idle-spark-vs-RPM correction while dialing in.",
        ]
        if cam_points:
            actions.append(
                f"Cam ({cam_points.klass}): try idle timing ~"
                f"{cam_points.idle_timing_deg[0]}-{cam_points.idle_timing_deg[1]} deg and "
                f"idle target ~{cam_points.idle_rpm} rpm as a starting point.")
            actions += cam_notes[:1]
        return Prescription(
            stage, STAGE_TITLE[stage],
            "It runs, but the idle is hunting. Chasing VE on an unstable idle just "
            "bakes the instability into the table -- steady the idle first.",
            actions=actions,
            drive="Warm it up and let it idle in park for ~2 minutes, A/C off.",
            capture=(_GM_CHANNELS if gm else
                     ["RPM", "MAP", "AFR", "Target AFR", "CTS", "IAC"]) +
                    ["(hold a steady warm idle -- no blipping the throttle)"],
        )

    if stage == "DIAL_IDLE_CRUISE":
        actions = ["Nothing to change yet -- collect a driving log first."]
        if airflow_mode == "ve_sd":
            actions.append("Reminder: for the first fuel pass, DISABLE the MAF "
                           "(force speed-density) so the trims reflect VE only.")
        return Prescription(
            stage, STAGE_TITLE[stage],
            "Stable idle, but the log has little or no driving data, so the cruise "
            "and load cells of the table are empty. Time to go populate them.",
            actions=actions,
            drive="Gentle drive: steady cruise at ~5 part-throttle speeds "
                  "(25/35/45/55/65), a few minutes each, light accel between. No WOT.",
            capture=_GM_CHANNELS if gm else
                    ["RPM", "MAP", "AFR", "Target AFR", "CL Comp", "Learn", "CTS", "TPS"],
        )

    if stage == "TUNE_VE_SD":
        if not gm:        # Holley: self-learning base fuel, no VE/MAF/SD phases
            actions = [
                "Transfer the learned correction into the BASE FUEL table (in the Holley "
                "software, 'Modify > Transfer Learn to Base Fuel' -- WITHOUT smoothing).",
                "Then reset/clear the Learn table so it starts fresh from the new base.",
                "Once it's stable, tighten the Learn parameters.",
            ]
            if summary.focus:
                actions.append(f"Biggest error is in {summary.focus}.")
            return Prescription(
                stage, "Dial in base fuel",
                f"The ECU has been self-correcting (median {summary.median_pct:+.1f}%). "
                "Fold that into the base fuel table so it isn't leaning on Learn, then "
                "re-log to confirm CL-comp and Learn stay near zero.",
                actions=actions,
                drive="Re-drive a similar mix (idle, cruise, a couple pulls) after baking "
                      "in the change and clearing Learn.",
                capture=["RPM", "MAP", "AFR", "Target AFR", "CL Comp", "Learn",
                         "Inj PW", "Duty Cycle", "CTS", "MAT", "Knock Retard"])
        off = summary.offset or {}
        actions = []
        if gen4:
            # Gen 4 has no real VE table -- it's Virtual VE (coefficients). Pros
            # tune MAF-only instead (docs/TUNING_BY_PLATFORM.md).
            actions.append("GEN 4 (E38/E67): there's no editable VE table -- it's "
                           "Virtual VE. Don't chase VVE. Set 'Dynamic Airflow High-RPM "
                           "Disable = 0' to run MAF-only, then tune the MAF curve against "
                           "AFR-error vs frequency (iterate to ~0-3% error).")
            actions.append("Paste the % error below onto the MAF curve, not a VE table. "
                           "Gen 4 also likes LESS timing (~23 deg ceiling, CR-dependent) "
                           "and a slightly leaner WOT (~12.5).")
        elif gen5:
            actions.append("GEN 5 (LT, direct injection): airflow is VVE + a DI fuel "
                           "model, and driver-demand torque management will fight mods. "
                           "Use the % error below as airflow guidance, but expect to also "
                           "raise torque/driver-demand limits and mind the DI fuel ceiling.")
        if gm and not gen4 and not gen5 and airflow_mode == "maf":
            actions.append("Heads up: you're set to MAF mode, but the cruise is still "
                           "shifted across the whole map -- that's VE-TABLE error, not the "
                           "MAF curve. Switch airflow to 'VE / MAF off', dial this VE pass "
                           "in first, THEN come back and tune the MAF curve.")
        elif gm and not gen4 and not gen5 and airflow_mode == "ve_sd":
            actions.append("MAF should be DISABLED for this pass (speed-density only) "
                           "so these trims are pure VE error.")
        if not gen4 and not gen5:
            if off.get("shape") == "global_offset":
                actions.append(
                    f"The correction is nearly FLAT (~{off.get('median_pct')}% across "
                    f"{off.get('n_cells')} cells). That's a GLOBAL offset -- fix it with a "
                    "single scalar (injector flow / fuel pressure / SD multiplier) before "
                    "reshaping the VE table.")
            else:
                actions.append(
                    "The correction VARIES across RPM/MAP -- it's a VE table-SHAPE issue. "
                    "Paste the multiplier grid into your Main VE table "
                    + ("(multiply-by-percent)." if gm else "(base fuel table)."))
        if summary.o2_suspect_cells:
            actions.append(
                f"{summary.o2_suspect_cells} cell(s) flagged O2/STOICH: the wideband "
                "disputes the narrowband. Resolve the O2 sensor / fuel-content (stoich) "
                "FIRST -- don't chase airflow there.")
        if "KNOCK_RETARD" in summary.safety_types:
            actions.append("KNOCK was logged -- review timing before trusting WOT cells.")
        return Prescription(
            stage, STAGE_TITLE[stage],
            f"Your VE/fuel is still off (biggest change in {summary.focus or 'the grid'}). "
            "Apply the correction, then re-log the same drive to watch the trims shrink.",
            actions=actions,
            drive="Re-drive the same cruise loop so cells line up pass-to-pass. "
                  "Add time in any thin/empty cells.",
            capture=_GM_CHANNELS if gm else
                    ["RPM", "MAP", "AFR", "Target AFR", "CL Comp", "Learn", "CTS", "TPS"],
        )

    if stage == "TUNE_MAF":
        return Prescription(
            stage, STAGE_TITLE[stage],
            "VE (speed-density) is dialed -- the cruise trims are centered. This step is "
            "the MAF curve, which is a DIFFERENT table from the VE table you just did.",
            actions=[
                "Two different tables, don't mix them up: the VE table is the RPM x MAP "
                "grid you already corrected in speed-density. The MAF table is a SINGLE "
                "ROW indexed by frequency (Hz) -- in HP Tuners it's 'Airflow vs "
                "Frequency'. This step edits the MAF table, NOT the VE table.",
                "Re-enable the MAF (undo the SD-only/disable change from the VE pass).",
                "Apply the frequency-indexed correction (Hz -> %) to that MAF 'Airflow "
                "vs Frequency' row.",
                "You MUST log the MAF Frequency (Hz) channel for this -- without it there's "
                "no way to build the curve correction. If it's not in the log, add it and "
                "re-capture.",
                "MAF is a steady-state sensor: trust steady cruise cells, ignore transients.",
            ],
            drive="Steady cruise sweep that walks airflow up smoothly (gentle, "
                  "progressive part-throttle through the gears). Hold steady speeds.",
            capture=(_GM_CHANNELS if gm else ["RPM", "MAP", "AFR", "Target AFR", "CTS"]) +
                    ["MAF Frequency (Hz)", "MAF airflow", "SD/Dynamic airmass"],
        )

    if stage == "TUNE_POWER":
        return Prescription(
            stage, STAGE_TITLE[stage],
            "Cruise is converged. The high-load / WOT cells are open-loop (no trims), "
            "so the only valid fuel feedback there is a wideband.",
            actions=[
                "Confirm a working wideband is logging -- WOT correction depends on it.",
                "Sanity-check WOT commanded AFR is safe (rich, ~12.5-12.8 pump) and "
                "knock retard is near zero before leaning anything.",
            ],
            drive="Safe WOT pulls: 2-3 runs in a mid gear from ~2500 rpm to redline, "
                  "clear road, fully warmed up. Back off if knock shows.",
            capture=(_GM_CHANNELS if gm else ["RPM", "MAP", "AFR", "Target AFR", "CTS"]) +
                    ["Wideband AFR (required)", "Knock retard"],
        )

    if stage == "TUNE_SPARK":
        actions, rationale = _spark_actions(spark, cam_points)
        return Prescription(
            stage, STAGE_TITLE[stage], rationale, actions=actions,
            drive="Repeat the WOT pulls after EACH small timing change -- one change, "
                  "one pull, re-log. Never add a chunk and send it.",
            capture=(_GM_CHANNELS if gm else ["RPM", "MAP", "AFR", "Target AFR", "CTS"]) +
                    ["Knock retard (required)", "Spark advance (actual)",
                     "Commanded/Desired spark timing (enables commanded-vs-actual check)",
                     "Wideband AFR", "IAT"],
        )

    # CONVERGED
    return Prescription(
        stage, STAGE_TITLE[stage],
        "Corrections are tiny across the populated map -- this tune has converged. "
        "From here it's maintenance: re-check after big weather/fuel/altitude swings.",
        actions=["No changes recommended. Save this tune as a known-good baseline."],
        drive="Optional: a mixed verification drive (cruise + a couple WOT pulls) "
              "to confirm it holds.",
        capture=_GM_CHANNELS if gm else
                ["RPM", "MAP", "AFR", "Target AFR", "Learn", "CTS"],
        converged=True,
    )


def _spark_actions(spark, cam_points):
    """Build the spark prescription from a spark.SparkResult."""
    if spark is None or not getattr(spark, "can_run", False):
        reason = getattr(spark, "reason", "") if spark else ""
        return ([reason or "Log a knock-retard channel on a WOT pull to enable spark work."],
                "Spark tuning needs knock feedback before it can recommend anything.")
    actions = []
    if spark.knock_cells:
        actions.append(
            f"PULL timing where knock showed ({spark.knock_cells} cell(s)) -- the grid "
            f"includes a +{2.0:g} deg safety margin beyond the observed retard. "
            "If a knock cell is flagged LEAN, fix fueling there FIRST; if HOT, address "
            "IAT / charge temp before chasing the timing.")
    has_add = (spark.action is not None and (spark.action.stack() == "ADD").any())
    if has_add:
        actions.append(
            "ADD the small (+1 deg) increments only in the marked power-region cells, "
            "then re-log. Stop when torque flattens or knock appears, then back off 2 deg.")
    if not spark.knock_cells and not has_add:
        actions.append("No knock and no headroom flagged -- timing looks well-placed. "
                       "Enable 'find power' if you want to probe for MBT.")
    actions.append(spark.advisory)
    if cam_points and cam_points.klass in ("mild", "big"):
        actions.append(f"Cam ({cam_points.klass}): low-RPM/light-load usually tolerates a "
                       "touch more advance -- probe gently there too, knock-verified.")
    rationale = ("Fuel is dialed, so timing is safe to work. Spark is knock-governed: "
                 "pulls are confident, adds are tiny and earned one pull at a time.")
    return actions, rationale
