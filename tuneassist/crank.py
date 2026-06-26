"""
crank.py -- "why didn't it start?" diagnosis for crank/no-start logs.

Triage already classifies a log as CRANKING_NO_START (it spun but never caught)
or STARTED_STALLED (caught then died). This reads the channels DURING the crank
window to turn that state into specific causes: did it get fuel (injector pulse,
fuel pressure), spark (timing logged), was it flooded or bone-dry (wideband),
was cranking speed too low (weak battery)? A start needs fuel + spark + sync +
compression at adequate cranking speed -- we check what the log can see and tell
the user exactly what to confirm next.

Recommendation-only, like everything else; degrades gracefully when channels are
absent (and then points at what to log -- see channels_ref).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .diagnostics import Finding


def _num(df, col, *keys):
    """First present key -> numeric Series, else None. Accepts GM and Holley keys."""
    for k in keys:
        if k in col:
            return pd.to_numeric(df[col[k]], errors="coerce")
    return None


def diagnose_no_start(df: pd.DataFrame, col: dict, cfg, state: str) -> list:
    """Return ranked Findings explaining a crank/no-start (or catch-and-die)."""
    findings: list = []
    rpm = _num(df, col, "rpm")
    if rpm is None:
        return findings
    rpm = rpm.fillna(0)

    # the cranking window: spinning but below catch. (For catch-and-die there is
    # also a brief run; the crank window is still where the no-catch story is.)
    crank = (rpm > 60) & (rpm < 500)
    if crank.sum() < 5:
        crank = rpm > 60                      # fallback: any rotation
    if crank.sum() < 3:
        return findings
    sub = df[crank.values]
    crank_rpm = float(rpm[crank].median())

    def cmean(series):
        if series is None:
            return None
        v = series[crank.values].dropna()
        return float(v.mean()) if len(v) else None

    inj = cmean(_num(df, col, "injpw"))
    duty = cmean(_num(df, col, "duty"))
    fp = cmean(_num(df, col, "fuelpres"))
    spark = _num(df, col, "spark", "ign")
    spark_present = spark is not None and spark[crank.values].notna().any()
    afr = cmean(_num(df, col, "afr_actual"))
    batt = cmean(_num(df, col, "battery"))
    have_fuel_signal = inj is not None or fp is not None

    # --- FUEL: injector pulse ---
    if inj is not None:
        if inj < 0.1:
            findings.append(Finding(
                "NOSTART_NO_INJECTION", "critical",
                "No injector pulse while cranking",
                f"Injector pulse width is ~{inj:.2f} ms during crank -- the ECU isn't "
                "commanding fuel.",
                ["Cam/crank SYNC not established (no sync = no sequential injection)",
                 "Cranking/prime fuel zeroed, or a disabled/failed injector driver",
                 "An immobilizer/anti-theft cut, or the ECU not seeing crank position"],
                ["Confirm SYNC during crank first -- it's the usual cause of zero pulse.",
                 "Check the cranking-fuel / prime tables aren't zeroed.",
                 "Verify injector power + driver wiring."], "high"))
        else:
            findings.append(Finding(
                "NOSTART_INJECTION_OK", "info",
                "Injectors are pulsing",
                f"Injector pulse ~{inj:.2f} ms during crank, so fuel is being commanded "
                "-- look at spark/sync and fuel pressure for the no-start.",
                [], [], "high"))

    # --- FUEL: rail pressure ---
    if fp is not None:
        if fp < 20:
            findings.append(Finding(
                "NOSTART_LOW_FUEL_PRESSURE", "critical",
                "Fuel pressure is low while cranking",
                f"Fuel pressure reads ~{fp:.0f} during crank -- an EFI engine wants "
                "roughly 40-60 psi to start.",
                ["Pump not priming/running (relay, wiring, fuse)",
                 "Dead pump, clogged filter, or a stuck regulator",
                 "Pressure sensor reading wrong"],
                ["Confirm the pump primes on key-on and holds pressure while cranking.",
                 "Check the pump relay/fuse and regulator."], "medium"))
        else:
            findings.append(Finding(
                "NOSTART_FUEL_PRESSURE_OK", "info", "Fuel pressure looks adequate",
                f"~{fp:.0f} psi during crank -- enough to start on; chase spark/sync instead.",
                [], [], "medium"))

    # --- combustion tell: wideband during crank ---
    if afr is not None:
        if afr < 10.0:
            findings.append(Finding(
                "NOSTART_FLOODED", "warning", "Reads very rich while cranking (flooded)",
                f"Wideband ~{afr:.1f} AFR during crank -- it's getting fuel but is likely "
                "flooded (or the wideband is just rich from raw fuel, not combustion).",
                ["Too much cranking/prime fuel", "No spark to burn it (wet plugs)",
                 "Leaking injector(s)"],
                ["Clear-flood (hold throttle wide while cranking) and pull cranking fuel.",
                 "If it stays rich with no catch, suspect spark/sync -- fuel's there, "
                 "it isn't burning."], "low"))
        elif afr > 17.0:
            findings.append(Finding(
                "NOSTART_STARVED", "warning", "Reads very lean while cranking (no fuel burning)",
                f"Wideband ~{afr:.1f} AFR during crank -- little or no fuel is reaching/"
                "burning in the cylinders.",
                ["No injector pulse or no fuel pressure (see above)",
                 "Way too little cranking fuel", "A big vacuum/intake leak"],
                ["Confirm injector pulse + fuel pressure during crank.",
                 "Add cranking fuel if both are present but it's still lean."], "low"))

    # --- SPARK / SYNC ---
    if spark_present:
        findings.append(Finding(
            "NOSTART_SPARK_LOGGED", "info", "Timing is being logged during crank",
            "A spark-advance value is present while cranking, so the ECU has sync and is "
            "scheduling spark. Confirm it's actually reaching the plugs (coils/wiring).",
            [], [], "medium"))
    else:
        findings.append(Finding(
            "NOSTART_SYNC_SUSPECT", "critical", "Can't confirm spark / cam-crank sync",
            "No spark-timing channel is logged, so the log can't confirm the ECU has "
            "cam/crank SYNC -- the #1 no-start cause on a fresh setup.",
            ["Cam/crank sync not established (trigger wheel, sensor wiring, ECU setup)",
             "No spark at the plugs (coil power/wiring)"],
            ["Log Sync Status + Ignition Timing and re-crank -- that confirms sync at a glance.",
             "Verify spark at a plug during crank."], "medium"))

    # --- cranking speed / battery ---
    if crank_rpm < 150:
        detail = f"Cranking speed is only ~{crank_rpm:.0f} rpm"
        detail += (f" and battery ~{batt:.1f} V under crank" if batt is not None and batt < 11
                   else "")
        findings.append(Finding(
            "NOSTART_SLOW_CRANK", "warning", "Cranking speed is low",
            detail + " -- a slow crank can keep it from catching and skews fuel/spark timing.",
            ["Weak/low battery", "Bad grounds or starter", "High-compression engine + marginal battery"],
            ["Charge/replace the battery and check grounds, then re-crank.",
             "Healthy cranking is ~180-250 rpm."], "low"))

    # --- the checklist / what-to-log catch-all ---
    if not have_fuel_signal and not spark_present:
        findings.append(Finding(
            "NOSTART_LOG_MORE", "warning", "Log the no-start essentials to pinpoint it",
            "This log doesn't have the channels that explain a no-start, so the cause "
            "can't be narrowed from data alone.",
            ["A start needs fuel + spark + sync + compression at a decent cranking speed."],
            ["Log these and re-crank: Sync Status, Injector Pulse Width, Ignition Timing, "
             "Fuel Pressure, a Wideband, and Battery Voltage.",
             "Then the log can tell you which of fuel / spark / sync is missing."], "high"))

    # rank: critical first, then warnings, then the reassuring info items
    rank = {"critical": 0, "warning": 1, "opportunity": 2, "info": 3}
    findings.sort(key=lambda f: rank.get(f.severity, 9))
    return findings
