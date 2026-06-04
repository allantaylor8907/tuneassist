"""
triage.py -- preflight vehicle-state classifier.

Before any fuel/VE correction is meaningful the engine has to actually be
running. A lot of real logs -- especially on a project car or a fresh swap --
are short captures of a crank attempt, a stall, or a hunting idle. Running the
VE correction on those produces garbage, so triage runs FIRST and either:

  * returns a state that GATES correction (anything below RUNNING_DRIVE), with a
    targeted "do this next" recommendation, or
  * clears the log to proceed to correction.

State ladder (each implies the ones below it failed):

  NO_DATA            no usable RPM channel / too few samples
  NO_CRANK           engine never turned over (RPM stays ~0)
  CRANKING_NO_START  RPM reaches cranking band but never catches
  STARTED_STALLED    RPM caught (>run threshold) then fell back to 0
  UNSTABLE_IDLE      sustained running but idle hunts / never stabilizes
  IDLE_ONLY          stable idle, but no driving data (no load/RPM range)
  RUNNING_DRIVE      warm, varied operating data -> proceed to correction

Thresholds are deliberately conservative and configurable; LS/Gen-3-4 and
Holley Terminator/Sniper all crank ~120-300 rpm and "run" north of ~500-600.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class TriageThresholds:
    min_samples: int = 20
    crank_rpm: float = 80.0       # above this = at least spinning
    run_rpm: float = 550.0        # above this (sustained) = caught/running
    run_hold_s: float = 2.0       # must stay >run_rpm this long to count as "started"
    idle_hunt_rpm: float = 150.0  # std above this at idle = hunting
    drive_rpm_span: float = 1200.0  # RPM range that indicates actual driving
    drive_map_span: float = 30.0  # kPa range that indicates load variation


@dataclass
class TriageResult:
    state: str
    can_correct: bool
    detail: str
    recommendations: list


def _sustained_above(mask: np.ndarray, t: np.ndarray, hold_s: float) -> bool:
    """True if `mask` is continuously True for at least hold_s seconds anywhere."""
    if not mask.any():
        return False
    run_start = None
    for i in range(len(mask)):
        if mask[i]:
            if run_start is None:
                run_start = i
            elif t[i] - t[run_start] >= hold_s:
                return True
        else:
            run_start = None
    return False


def triage(df: pd.DataFrame, col: dict, th: TriageThresholds | None = None) -> TriageResult:
    th = th or TriageThresholds()

    if "rpm" not in col or len(df) < th.min_samples:
        return TriageResult("NO_DATA", False,
                            "No RPM channel or too few samples to assess.",
                            ["Log RPM (and ideally sync status) and capture at least "
                             "a few seconds of data."])

    rpm = pd.to_numeric(df[col["rpm"]], errors="coerce").fillna(0).to_numpy()
    t = (pd.to_numeric(df[col["time"]], errors="coerce").to_numpy()
         if "time" in col else np.arange(len(rpm)) * 0.05)
    rpm_max = float(np.nanmax(rpm))

    # NO_CRANK: never even spun. But a WARM engine reading 0 rpm didn't fail to
    # crank -- it ran; the RPM/tach signal just isn't reaching the controller.
    if rpm_max < th.crank_rpm:
        ect_max = (pd.to_numeric(df[col["ect"]], errors="coerce").max()
                   if "ect" in col else float("nan"))
        if ect_max == ect_max and ect_max > 140:     # warm: it clearly ran
            return TriageResult("NO_CRANK", False,
                f"RPM reads ~0 but coolant is warm ({ect_max:.0f}F) -- the engine ran, "
                "so the RPM/tach signal isn't reaching the controller.",
                ["The engine is fine (it warmed up); the RPM input is the suspect -- "
                 "check the tach/crank-signal wiring and the RPM setup (cylinders, "
                 "trigger type).",
                 "Without an RPM signal the ECU can't schedule fuel/spark by RPM or log "
                 "anything useful -- fix that first, then re-log."])
        return TriageResult("NO_CRANK", False,
            f"Engine never turned over (max {rpm_max:.0f} rpm).",
            ["Confirm the starter is cranking the engine (battery voltage under load, "
             "starter/relay, ignition switch).",
             "If it cranks on the key but the log shows ~0 rpm, the crank position "
             "sensor or its wiring/reluctor is the prime suspect -- the ECU sees no rotation.",
             "Verify the ECU is powered and seeing the crank sensor before anything else."])

    caught = _sustained_above(rpm > th.run_rpm, t, th.run_hold_s)

    # CRANKING_NO_START: spins in the crank band but never catches.
    if not caught:
        return TriageResult("CRANKING_NO_START", False,
            f"Engine cranks (max {rpm_max:.0f} rpm) but never caught.",
            ["Confirm cam/crank SYNC -- without sync the ECU won't schedule sequential "
             "fuel/spark. This is the #1 no-start cause on a fresh setup.",
             "Verify spark at the plugs during crank (coil power, trigger).",
             "Verify injectors are pulsing and you have fuel pressure (prime + cranking pulse).",
             "Check cranking fuel / afterstart enrichment isn't zeroed; flooded or bone-dry "
             "both show as crank-no-start -- read plugs to tell which."])

    # It caught at least once. Is there real DRIVING variation? If so the log is
    # usable -- even if it ends at 0 rpm (you logged through shutdown). A normal
    # shutdown tail gets filtered downstream; it is NOT a stall.
    running_mask = rpm > th.run_rpm
    running = rpm[running_mask]
    rpm_span = float(np.nanmax(running) - np.nanmin(running)) if running.size else 0.0
    map_span = 0.0
    if "map" in col:
        mp = pd.to_numeric(df[col["map"]], errors="coerce")
        map_span = float(mp.quantile(0.95) - mp.quantile(0.05))

    if rpm_span >= th.drive_rpm_span or map_span >= th.drive_map_span:
        return TriageResult("RUNNING_DRIVE", True,
            f"Warm, varied operating data (RPM span {rpm_span:.0f}, MAP span {map_span:.0f} kPa).",
            ["Proceed to fuel/VE correction."])

    # No real driving. A true catch-and-die stall ran only BRIEFLY then dropped to
    # ~0 -- distinguish that from an idle that the user simply shut off at the end.
    run_t = t[running_mask]
    ran_dur = float(np.nanmax(run_t) - np.nanmin(run_t)) if run_t.size else 0.0
    ended_dead = (rpm[-int(len(rpm) * 0.1):] < th.crank_rpm).all()
    if ended_dead and ran_dur < 25.0:
        return TriageResult("STARTED_STALLED", False,
            f"Engine caught (peaked {rpm_max:.0f} rpm) then died after ~{ran_dur:.0f}s.",
            ["Raise idle airflow first -- IAC steps / throttle blade stop. Most "
             "catch-and-die is simply not enough air to sustain idle.",
             "Check afterstart and idle base fuel: too rich or too lean both stall.",
             "Confirm idle spark advance is reasonable (very low advance won't hold idle).",
             "Re-log a longer start attempt once idle air is bumped up."])

    # Essentially idle-only: now idle stability is the question that matters.
    idle_sel = (rpm > th.run_rpm) & (rpm < th.run_rpm + 800)   # idle band, not driving
    if "tps" in col:
        tps = pd.to_numeric(df[col["tps"]], errors="coerce").fillna(0).to_numpy()
        idle_sel = idle_sel & (tps <= np.nanpercentile(tps, 25) + 3)
    idle_rpm = rpm[idle_sel]
    idle_std = float(np.std(idle_rpm)) if idle_rpm.size > 10 else float(np.std(running))

    if idle_std > th.idle_hunt_rpm:
        return TriageResult("UNSTABLE_IDLE", False,
            f"Runs, but idle is hunting (idle RPM std {idle_std:.0f}).",
            ["Stabilize idle before fueling: set a sane idle airflow target and IAC range.",
             "Check for vacuum leaks (lean hunt) and over-rich idle (loading up).",
             "Idle spark vs RPM error correction can amplify hunt -- soften it while dialing in.",
             "Get a steady idle, then re-log for idle/cruise VE work."])

    return TriageResult("IDLE_ONLY", True,
        f"Stable idle (idle RPM std {idle_std:.0f}) but little driving data "
        f"(RPM span {rpm_span:.0f}, MAP span {map_span:.0f} kPa).",
        ["Idle VE can be assessed, but cruise/WOT cells will be empty.",
         "Drive it: steady cruise at several speeds, then some load, to populate the map."])
