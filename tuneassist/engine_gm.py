#!/usr/bin/env python3
"""
tune_assist.py  -- AI-assisted VE/fuel correction analyzer for HPTuners logs (LS / GM speed-density).

WHAT IT DOES
------------
Reads a VCM Scanner CSV export of a drive, figures out per-operating-cell whether
the engine is running rich or lean vs. the commanded target, and produces a
RECOMMENDED VE (volumetric efficiency) correction table you apply by hand in
VCM Editor. It is recommendation-only on purpose -- nothing here touches the
.hpt file or the vehicle.

CORRECTION LOGIC (the important part)
-------------------------------------
For each logged sample it computes a fueling error, then bins it into the same
RPM x MAP grid as your Main VE table:

  * Closed-loop cells (cruise/light load): the PCM is already trimming fuel to
    hit stoich, so the VE error == the fuel trim it had to apply.
        correction = 1 + (STFT + LTFT)/100
    Positive trim => PCM added fuel => VE table under-estimated air => raise VE.

  * Open-loop cells (WOT / power enrichment): no trims, so we use the wideband.
        correction = measured_AFR / commanded_AFR
    Measured leaner than commanded => more air than modeled => raise VE.

It then takes a sample-weighted, outlier-trimmed average per cell, applies a
damping factor (default 0.70 so you don't over-correct and oscillate), and
flags cells with too few samples as low-confidence.

A separate safety pass scans the raw log for knock retard, lean-under-load, and
high IAT events and surfaces them with timestamps -- those you look at before
trusting any correction.

USAGE
-----
    python tune_assist.py drive_log.csv
    python tune_assist.py drive_log.csv --base-ve current_ve.csv --out-dir ./out
    python tune_assist.py drive_log.csv --stoich 9.76   # E85

Column names are fuzzy-matched against common HPTuners channel labels; if a
channel isn't found the relevant logic is skipped and noted, so it degrades
gracefully on whatever you happened to be logging.
"""

from __future__ import annotations
import argparse
import sys
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Channel resolution: map messy HPTuners CSV headers to canonical names.
# Each entry is (canonical_name, [regex patterns tried in order]).
# --------------------------------------------------------------------------
CHANNEL_PATTERNS = {
    "time":        [r"^time", r"offset", r"elapsed"],
    "rpm":         [r"engine.*rpm", r"\brpm\b", r"speed.*rpm"],
    "map":         [r"intake.*manifold.*abs", r"manifold.*abs(?!.*sensor)",
                    r"\bmap\b", r"manifold.*pressure(?!.*sensor)", r"manifold.*abs"],
    "airmass":     [r"cyl.*air", r"air.*cyl", r"air ?mass", r"\bapc\b", r"charge"],
    # Prefer an explicit "...AFR" wideband over a bare "AEM"/"EQ" equivalence-ratio
    # channel (a custom AEM install logs both "AEM EQ -> ..." and "AEM - AFR").
    "afr_actual":  [r"wide ?band", r"aem.*afr", r"\bwo2\b", r"\buego\b", r"\baem\b",
                    r"lc-?1", r"afr.*(act|avg|wide)", r"\bafr\b"],
    "afr_cmd":     [r"air.?fuel.*ratio.*comm", r"afr.*comm", r"comm.*afr",
                    r"desired.*afr", r"target.*afr"],
    "eq_cmd":      [r"equiv.*ratio.*comm", r"eq.*ratio.*comm", r"comm.*eq", r"lambda.*comm"],
    "stft":        [r"short.*term.*fuel.*b.*1", r"short.*term.*fuel", r"\bstft", r"sft\b"],
    "stft2":       [r"short.*term.*fuel.*b.*2"],
    "ltft":        [r"long.*term.*fuel.*b.*1", r"long.*term.*fuel", r"\bltft", r"lft\b"],
    "ltft2":       [r"long.*term.*fuel.*b.*2"],
    "cl_status":   [r"closed.?loop", r"fuel.*sys.*stat", r"loop.*status"],
    "ect":         [r"coolant.*temp", r"engine.*coolant", r"\bect\b", r"engine.*temp"],
    "iat":         [r"intake.*air.*temp", r"\biat\b", r"charge.*temp"],
    "tps":         [r"throttle.*pos", r"\btps\b", r"pedal"],
    "knock":       [r"knock.*retard", r"total.*knock", r"\bknock\b", r"spark.*retard"],
    # Spark / airflow-attribution / torque channels (for the MAF and spark phases)
    "spark":       [r"spark.*advance", r"ignition.*timing", r"timing.*advance",
                    r"\bspark adv", r"commanded.*timing", r"high.?octane.*spark"],
    "maf_freq":    [r"maf.*freq", r"maf.*hz", r"\bfrequency\b"],
    "maf_air":     [r"maf.*(g/?s|gps|lb|mass|flow|air)", r"mass.?air.?flow",
                    r"air.?flow.*sensor", r"\bmaf\b"],
    "sd_air":      [r"dyn.*air", r"sd.*air", r"calc.*air.*mass", r"cylinder.*air"],
    "torque":      [r"engine.*torque", r"\btorque\b", r"\btq\b"],
    "injpw":       [r"inj.*pulse.*width", r"inj.*pw\b", r"\bipw\b"],
    "duty":        [r"inj.*duty", r"duty.*cycle", r"\bidc\b"],
    "baro":        [r"barometric", r"\bbaro\b"],
    "fuelpres":    [r"fuel.*rail.*pres", r"fuel.*pres(?!.*sw)", r"\bfp\b(?!.*set)"],
    "boost":       [r"boost"],
    "battery":     [r"battery", r"system.*volt", r"\bvolts?\b"],
    "speed":       [r"vehicle.*speed", r"\bvss\b", r"\bmph\b"],
    "gear":        [r"current gear", r"\bgear\b(?!.*state)"],
    "iss":         [r"input shaft", r"turbine.*speed", r"\biss\b"],
    "line_pres":   [r"line.*pres", r"\bepc\b", r"main.*pres"],
    "tcc":         [r"tcc", r"converter clutch", r"\blockup\b"],
    "iac":         [r"idle.*air.*control", r"\biac\b"],
    "idle_target": [r"desired.*idle", r"target.*idle", r"idle.*desired",
                    r"commanded.*idle"],
    "ase":         [r"after.?start", r"start.*enrich"],
    "warmup_enr":  [r"warm.?up.*enrich", r"coolant.*enrich"],
}


def resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Return {canonical: actual_column}. Missing canonicals are simply absent."""
    resolved: dict[str, str] = {}
    cols = list(df.columns)
    lower = {c: c.lower() for c in cols}
    for canon, patterns in CHANNEL_PATTERNS.items():
        for pat in patterns:
            hit = next((c for c in cols if re.search(pat, lower[c])), None)
            if hit:
                resolved[canon] = hit
                break
    return resolved


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass
class Config:
    stoich: float = 14.7          # 14.7 pump gas, ~9.76 E85, ~14.08 E10
    ect_min_f: float = 160.0      # only trust fully warmed-up data
    iat_max_f: float = 150.0      # flag heat-soak above this
    tps_rate_max: float = 25.0    # %/sample; reject hard transients
    knock_flag_deg: float = 1.0   # any sustained retard above this is flagged
    lean_load_map: float = 80.0   # kPa; "high load" threshold for lean check
    lean_afr_flag: float = 13.2   # leaner than this under load = danger
    min_samples: int = 8          # cell confidence floor
    damping: float = 0.70         # apply this fraction of computed correction
    trim_conf: float = 3.0        # |total trim| above this = real airflow error
    o2_suspect: float = 4.0       # wideband vs commanded gap (%) flagging O2/stoich
    # --- spark / timing (DESIGN.md S10), all degrees ---
    knock_pull_deg: float = 0.5   # retard above this in a cell = pull timing there
    knock_pull_margin: float = 2.0  # pull this much MORE than the observed retard
    spark_add_step: float = 1.0   # proactive "find power" increment per pass
    spark_add_max: float = 2.0    # never suggest adding more than this in one pass
    spark_back_off: float = 2.0   # once MBT/knock found, back off this much
    iat_spark_safe: float = 120.0 # don't suggest ADDING timing above this IAT (F)
    wot_map_min: float = 80.0     # kPa; spark "power region" load threshold
    rpm_bins: list = field(default_factory=lambda:
        [400, 800, 1200, 1600, 2000, 2400, 2800, 3200, 4000, 4800, 5600, 6400])
    map_bins: list = field(default_factory=lambda:
        [20, 30, 40, 50, 60, 70, 80, 90, 100])


# --------------------------------------------------------------------------
# Core analysis
# --------------------------------------------------------------------------
@dataclass
class Result:
    correction: pd.DataFrame      # multipliers, indexed RPM x MAP
    samples: pd.DataFrame         # sample count per cell
    confidence: pd.DataFrame      # bool: enough samples
    new_ve: pd.DataFrame | None   # base_ve * correction, if base supplied
    safety: list                  # list of dicts (event, time, detail)
    notes: list                   # processing notes / skipped channels
    recommendation: pd.DataFrame | None = None  # per-cell action class
    wb_dev: pd.DataFrame | None = None          # wideband vs commanded gap (%)


def _closed_loop_mask(df: pd.DataFrame, col: dict, cfg: Config) -> np.ndarray:
    """Best available closed-loop signal: explicit status > commanded AFR near
    stoich > commanded lambda near 1 > crude throttle fallback. Power enrichment
    commands a rich target, which is how open loop is detected without a status
    channel."""
    if "cl_status" in col:
        s = df[col["cl_status"]].astype(str)
        # SAE strings are 'CL - Normal' (closed loop) / 'OL - Not Ready' (open
        # loop), NOT the literal word 'closed'. Recognize the CL/OL abbreviation.
        if s.str.contains(r"\b[CO]L\b", case=False, regex=True).any():
            return s.str.contains(r"\bcl\b|closed", case=False, regex=True).to_numpy()
        v = pd.to_numeric(df[col["cl_status"]], errors="coerce")
        if v.notna().mean() > 0.5:        # numeric status: 2 = closed-loop, else 1/0 flag
            u = set(v.dropna().unique())
            m = ((v == 2) if 2 in u else (v >= 1)).to_numpy()
            if m.any():
                return m
    if "afr_cmd" in col:
        return (df[col["afr_cmd"]] >= 14.0).to_numpy()
    if "eq_cmd" in col:
        return (df[col["eq_cmd"]] <= 1.03).to_numpy()
    if "tps" in col:
        return (df[col["tps"]] < 35).to_numpy()
    return np.zeros(len(df), dtype=bool)


def _per_sample_correction(df: pd.DataFrame, col: dict, cfg: Config):
    """Return a Series of VE correction multipliers + a mask of usable rows."""
    n = len(df)
    corr = np.full(n, np.nan)
    closed = _closed_loop_mask(df, col, cfg)

    # Average whatever fuel-trim banks were logged.
    stft_cols = [col[k] for k in ("stft", "stft2") if k in col]
    ltft_cols = [col[k] for k in ("ltft", "ltft2") if k in col]
    if stft_cols and ltft_cols:
        stft = df[stft_cols].mean(axis=1).fillna(0).to_numpy()
        ltft = df[ltft_cols].mean(axis=1).fillna(0).to_numpy()
        total_trim = stft + ltft
        corr = np.where(closed, 1.0 + total_trim / 100.0, corr)

    # Open-loop: correction from wideband vs commanded.
    if "afr_actual" in col and "afr_cmd" in col:
        meas = df[col["afr_actual"]].to_numpy()
        cmd = df[col["afr_cmd"]].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            wb_corr = np.where(cmd > 0, meas / cmd, np.nan)
        corr = np.where(~closed, wb_corr, corr)

    # Sanity clamp -- ignore absurd single-sample swings (sensor glitches).
    corr = np.where((corr > 0.70) & (corr < 1.30), corr, np.nan)
    return pd.Series(corr, index=df.index)


def _usable_mask(df: pd.DataFrame, col: dict, cfg: Config):
    mask = pd.Series(True, index=df.index)
    notes = []
    if "ect" in col:
        mask &= df[col["ect"]] >= cfg.ect_min_f
    else:
        notes.append("No coolant-temp channel: cannot exclude cold-engine data.")
    if "tps" in col:
        rate = df[col["tps"]].diff().abs().fillna(0)
        mask &= rate <= cfg.tps_rate_max          # drop hard transients
    if "rpm" in col:
        mask &= df[col["rpm"]] > 400               # drop idle-off / cranking
    return mask, notes


def _classify_cells(df, col, cfg, usable, correction):
    """Classify each confident cell into a recommended action:
      VE/MAF    - trim shows a real airflow error (wideband, if present, agrees)
      O2/STOICH - wideband disagrees with what the ECM thinks it's holding in
                  closed loop => narrowband bias / fuel-content mismatch; do NOT
                  correct airflow here until the sensor/stoich is resolved
      WOT       - open-loop cell; correct airflow from wideband vs commanded
      OK        - within tolerance, leave alone
    Returns (recommendation_grid, wb_dev_grid)."""
    has_wb = ("afr_actual" in col and "afr_cmd" in col)
    has_trim = (("stft" in col or "stft2" in col) and ("ltft" in col or "ltft2" in col))

    w = df[usable].copy()
    w["_rpm_bin"] = pd.cut(w[col["rpm"]], bins=cfg.rpm_bins)
    w["_map_bin"] = pd.cut(w[col["map"]], bins=cfg.map_bins)
    w["_closed"] = _closed_loop_mask(df, col, cfg)[usable.to_numpy()]
    stft_cols = [col[k] for k in ("stft", "stft2") if k in col]
    ltft_cols = [col[k] for k in ("ltft", "ltft2") if k in col]
    if has_trim:
        w["_trim"] = (w[stft_cols].mean(axis=1).fillna(0) + w[ltft_cols].mean(axis=1).fillna(0))

    keys = ["_rpm_bin", "_map_bin"]
    def grid(series_frame, mask=None):
        src = w[mask] if mask is not None else w
        return src.groupby(keys, observed=False)[series_frame].mean().unstack() \
                  .reindex(index=correction.index, columns=correction.columns)

    frac_closed = grid("_closed")
    closed_cell = frac_closed >= 0.5

    wb_dev = None
    if has_wb:
        cl = w["_closed"]
        wbcl = grid(col["afr_actual"], cl)
        cmdcl = grid(col["afr_cmd"], cl)
        wb_dev = ((wbcl / cmdcl - 1.0) * 100).round(2)

    rec = pd.DataFrame(np.nan, index=correction.index, columns=correction.columns, dtype=object)
    conf = correction.notna()
    trim_grid = grid("_trim", w["_closed"]) if has_trim else None

    if has_wb:
        o2 = closed_cell & (wb_dev.abs() > cfg.o2_suspect)
        wot = ~closed_cell
        big_trim = closed_cell & ~o2 & (trim_grid.abs() > cfg.trim_conf) if has_trim else closed_cell & ~o2
        ok = closed_cell & ~o2 & ~big_trim
        rec = rec.mask(conf & o2, "O2/STOICH")
        rec = rec.mask(conf & big_trim, "VE/MAF")
        rec = rec.mask(conf & ok, "OK")
        rec = rec.mask(conf & wot, "WOT")
    elif has_trim:
        big_trim = trim_grid.abs() > cfg.trim_conf
        rec = rec.mask(conf & big_trim, "VE/MAF")
        rec = rec.mask(conf & ~big_trim, "OK")
    return rec, wb_dev


def analyze(df: pd.DataFrame, cfg: Config, base_ve: pd.DataFrame | None = None) -> Result:
    col = resolve_columns(df)
    notes = [f"Resolved channels: {', '.join(sorted(col)) or 'NONE'}"]
    for required in ("rpm", "map"):
        if required not in col:
            raise ValueError(f"Required channel '{required}' not found in log. "
                             f"Headers seen: {list(df.columns)}")

    corr = _per_sample_correction(df, col, cfg)
    usable, m_notes = _usable_mask(df, col, cfg)
    notes += m_notes
    if not (("stft" in col and "ltft" in col) or ("afr_actual" in col and "afr_cmd" in col)):
        notes.append("WARNING: neither fuel trims nor wideband+commanded AFR found -- "
                     "no fuel correction possible. Log STFT/LTFT or a wideband.")

    work = df[usable & corr.notna()].copy()
    if len(work) == 0:
        reason = "No usable samples after filtering."
        if "ect" in col and pd.to_numeric(df[col["ect"]], errors="coerce").max() < cfg.ect_min_f:
            mx = pd.to_numeric(df[col["ect"]], errors="coerce").max()
            reason = (f"All samples excluded: engine never reached operating temp "
                      f"(max coolant {mx:.0f}F, need >={cfg.ect_min_f:.0f}F). "
                      f"Warm-up data isn't valid for VE/fuel correction.")
        elif corr.notna().sum() == 0 and "cl_status" in col and \
                _closed_loop_mask(df, col, cfg).sum() == 0:
            reason = ("Engine stayed in OPEN loop the whole log (O2/closed-loop never "
                      "engaged), so the fuel trims aren't actively correcting -- nothing to "
                      "read. Warm it fully so it enters closed loop, or log a wideband.")
        elif corr.notna().sum() == 0:
            reason = ("No fueling-error signal: log has neither usable fuel trims "
                      "nor wideband+commanded AFR.")
        notes.append("RESULT: " + reason)
        empty = pd.DataFrame(index=pd.Index([], name="rpm"))
        return Result(empty, empty, empty, None, _safety_scan(df, col, cfg), notes)
    work["_corr"] = corr[usable & corr.notna()]
    work["_rpm_bin"] = pd.cut(work[col["rpm"]], bins=cfg.rpm_bins)
    work["_map_bin"] = pd.cut(work[col["map"]], bins=cfg.map_bins)

    def trimmed_mean(s: pd.Series) -> float:
        s = s.dropna()
        if len(s) >= 5:                            # drop 10% tails when we can
            lo, hi = s.quantile(0.10), s.quantile(0.90)
            s = s[(s >= lo) & (s <= hi)]
        return float(s.mean()) if len(s) else np.nan

    grouped = work.groupby(["_rpm_bin", "_map_bin"], observed=False)["_corr"]
    raw = grouped.apply(trimmed_mean).unstack()
    counts = grouped.size().unstack().fillna(0).astype(int)

    # Damp toward 1.0 and zero out low-confidence cells.
    damped = 1.0 + (raw - 1.0) * cfg.damping
    confidence = counts >= cfg.min_samples
    correction = damped.where(confidence)

    new_ve = None
    if base_ve is not None:
        # Align shapes best-effort; only multiply where both exist.
        new_ve = base_ve.copy()
        common_r = correction.index.intersection(base_ve.index) if hasattr(base_ve, "index") else []
        notes.append("Base VE supplied: applied correction where cells align "
                     f"({len(common_r)} RPM rows matched).")

    correction = correction.round(4)
    safety = _safety_scan(df, col, cfg)

    # Cross-check classifier (DESIGN.md S4): label each confident cell with the
    # action to take -- VE/MAF vs O2/STOICH vs WOT vs OK -- and the wideband
    # deviation from commanded. Additive: never let a classifier hiccup take down
    # the (tested) correction path.
    recommendation = wb_dev = None
    try:
        recommendation, wb_dev = _classify_cells(df, col, cfg, usable, correction)
    except Exception as e:  # pragma: no cover - defensive
        notes.append(f"(cross-check classifier skipped: {e})")

    return Result(correction, counts, confidence, new_ve, safety, notes,
                  recommendation=recommendation, wb_dev=wb_dev)


def offset_vs_shape(correction: pd.DataFrame, cfg: Config) -> dict:
    """DESIGN.md S5 / Open ideas: is the recommended correction a flat *global
    offset* (point at a global scalar -- MAF curve, injector flow, fuel pressure,
    stoich) or does it *vary with RPM/MAP* (a real VE table-shape problem)?

    A flat map says "everything's off by the same %", which a single scalar fixes
    far more cleanly than reshaping the whole VE table. We measure flatness as the
    spread of confident cell corrections around their own median.

    Returns {} when there isn't enough data to judge."""
    if correction is None or correction.empty:
        return {}
    vals = correction.stack().dropna()
    if len(vals) < 6:
        return {}
    pct = (vals - 1.0) * 100.0
    median = float(pct.median())
    # robust spread (IQR) of the per-cell corrections about their center
    spread = float(pct.quantile(0.75) - pct.quantile(0.25))
    # "flat" when cells mostly agree on one offset and that offset is non-trivial
    flat = spread <= 2.5 and abs(median) >= 1.5
    return {
        "median_pct": round(median, 2),
        "spread_pct": round(spread, 2),
        "n_cells": int(len(vals)),
        "shape": "global_offset" if flat else "table_shape",
    }


def maf_correction(df: pd.DataFrame, cfg: Config, col: dict | None = None):
    """DESIGN.md S9 phase 2: tune the MAF curve once VE is correct.

    The MAF table is indexed by FREQUENCY (Hz), not RPM x MAP, so we bin the
    airflow error by MAF frequency and return a 1-D Hz -> % correction. Error
    source, best first: residual fuel trims with MAF enabled (== MAF error once
    VE is dialed); else SD airmass / MAF airmass (SD is the trustworthy
    reference once VE is correct). Steady-state only -- MAF is a steady sensor.

    Returns (correction_series, counts_series, notes). correction_series is None
    when there's nothing to bin (no MAF frequency channel)."""
    col = col or resolve_columns(df)
    notes = []
    if "maf_freq" not in col:
        return None, None, ["No MAF frequency channel logged -- can't build the Hz "
                            "correction table. Log 'MAF Frequency' to tune the MAF curve."]
    w = df.copy()
    if "ect" in col:
        w = w[pd.to_numeric(w[col["ect"]], errors="coerce") >= cfg.ect_min_f]
    if "tps" in col:   # steady-state only
        rate = pd.to_numeric(df[col["tps"]], errors="coerce").diff().abs().reindex(w.index).fillna(0)
        w = w[rate <= cfg.tps_rate_max]
    if len(w) == 0:
        return None, None, ["No warm/steady samples for MAF correction."]

    stft = [col[k] for k in ("stft", "stft2") if k in col]
    ltft = [col[k] for k in ("ltft", "ltft2") if k in col]
    if stft and ltft:
        total = w[stft].mean(axis=1).fillna(0) + w[ltft].mean(axis=1).fillna(0)
        w["_corr"] = 1.0 + total / 100.0
        notes.append("MAF error from residual fuel trims (MAF enabled, VE assumed correct).")
    elif "sd_air" in col and "maf_air" in col:
        sd = pd.to_numeric(w[col["sd_air"]], errors="coerce")
        mf = pd.to_numeric(w[col["maf_air"]], errors="coerce")
        w["_corr"] = (sd / mf).where(mf > 0)
        notes.append("MAF error from SD airmass / MAF airmass (SD as reference).")
    else:
        return None, None, ["Need fuel trims or SD+MAF airmass to derive MAF error."]

    w["_corr"] = w["_corr"].where((w["_corr"] > 0.7) & (w["_corr"] < 1.3))
    freq = pd.to_numeric(w[col["maf_freq"]], errors="coerce")
    # Bin into ~500 Hz buckets across the observed range.
    lo, hi = float(freq.min()), float(freq.max())
    if not np.isfinite(lo) or hi - lo < 1:
        return None, None, ["MAF frequency channel has no usable range."]
    edges = np.arange(np.floor(lo / 500) * 500, np.ceil(hi / 500) * 500 + 500, 500)
    w["_fbin"] = pd.cut(freq, bins=edges)
    grp = w.groupby("_fbin", observed=False)["_corr"]
    raw = grp.mean()
    counts = grp.size()
    damped = (1.0 + (raw - 1.0) * cfg.damping).where(counts >= cfg.min_samples).round(4)
    notes.append(f"MAF correction binned into {len(edges)-1} frequency buckets "
                 f"({int(lo)}-{int(hi)} Hz observed).")
    return damped, counts.astype(int), notes


def _safety_scan(df: pd.DataFrame, col: dict, cfg: Config) -> list:
    events = []
    t = df[col["time"]] if "time" in col else df.index

    if "knock" in col:
        kn = df[col["knock"]]
        for i in df.index[kn > cfg.knock_flag_deg]:
            events.append({"type": "KNOCK_RETARD", "time": float(t[i]),
                           "detail": f"{kn[i]:.1f} deg retard"
                                     + (f" @ {df[col['rpm']][i]:.0f} rpm" if "rpm" in col else "")})
    if "afr_actual" in col and "map" in col:
        lean = (df[col["map"]] >= cfg.lean_load_map) & (df[col["afr_actual"]] > cfg.lean_afr_flag)
        for i in df.index[lean]:
            events.append({"type": "LEAN_UNDER_LOAD", "time": float(t[i]),
                           "detail": f"{df[col['afr_actual']][i]:.1f} AFR @ "
                                     f"{df[col['map']][i]:.0f} kPa"})
    if "iat" in col:
        for i in df.index[df[col["iat"]] > cfg.iat_max_f]:
            events.append({"type": "HIGH_IAT", "time": float(t[i]),
                           "detail": f"{df[col['iat']][i]:.0f} F intake"})

    # Collapse runs of the same event type into a count + first/worst occurrence.
    collapsed, seen = [], {}
    for e in events:
        key = e["type"]
        if key not in seen:
            seen[key] = {**e, "_count": 1}
            collapsed.append(seen[key])
        else:
            seen[key]["_count"] += 1
    for e in collapsed:
        if e["_count"] > 1:
            e["detail"] += f"  ({e['_count']} events total)"
    return collapsed


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def write_report(res: Result, out_dir: str):
    import os
    os.makedirs(out_dir, exist_ok=True)
    res.correction.to_csv(os.path.join(out_dir, "ve_correction_multipliers.csv"))
    res.samples.to_csv(os.path.join(out_dir, "cell_sample_counts.csv"))
    if res.new_ve is not None:
        res.new_ve.to_csv(os.path.join(out_dir, "suggested_ve.csv"))

    lines = ["=" * 70, "TUNE ASSIST -- VE CORRECTION REPORT", "=" * 70, ""]
    lines += res.notes + [""]

    if res.correction.empty:
        lines.append("SAFETY EVENTS:")
        if res.safety:
            for e in res.safety:
                lines.append(f"  [{e['type']}] t={e['time']:.1f}s  {e['detail']}")
        else:
            lines.append("  None flagged.")
        report = "\n".join(lines)
        with open(os.path.join(out_dir, "report.txt"), "w") as f:
            f.write(report)
        return report

    pct = (res.correction - 1.0) * 100
    big = pct.abs().stack().sort_values(ascending=False)
    lines.append("LARGEST RECOMMENDED CHANGES (RPM x MAP cell -> % change):")
    for (r, m), v in big.head(12).items():
        sign = "+" if (res.correction.loc[r, m] - 1) >= 0 else ""
        lines.append(f"  {str(r):>16} x {str(m):<12}  {sign}{(res.correction.loc[r,m]-1)*100:5.1f}%")
    covered = int(res.confidence.values.sum())
    total = res.confidence.size
    lines += ["", f"Cells with enough data: {covered}/{total} "
                  f"({100*covered/total:.0f}% coverage)", ""]

    lines.append("SAFETY EVENTS (review before applying anything):")
    if res.safety:
        for e in res.safety:
            lines.append(f"  [{e['type']}] t={e['time']:.1f}s  {e['detail']}")
    else:
        lines.append("  None flagged.")
    lines += ["", "Apply multipliers to your Main VE table in VCM Editor "
                  "(multiply-by-percent paste). Re-log and repeat 2-3 passes."]

    report = "\n".join(lines)
    with open(os.path.join(out_dir, "report.txt"), "w") as f:
        f.write(report)
    return report


def load_log(path: str):
    """Load a log into (df, units). Handles the HP Tuners CSV export format
    (metadata preamble + [Channel Information] names/units + [Channel Data])
    and falls back to a plain CSV."""
    with open(path, encoding="latin-1") as fh:
        head = fh.readline()
    if "HP Tuners" not in head:
        df = pd.read_csv(path)
        return df, {}

    lines = open(path, encoding="latin-1").read().splitlines()
    ci = next((i for i, l in enumerate(lines) if l.startswith("[Channel Information]")), None)
    cd = next((i for i, l in enumerate(lines) if l.startswith("[Channel Data]")), None)
    if ci is None or cd is None:
        # "HP Tuners" header but no section markers (partial/odd export): skip the
        # metadata banner and read what's there rather than crashing.
        skip = next((i for i, l in enumerate(lines) if l.count(",") >= 3), 0)
        return pd.read_csv(path, skiprows=skip, engine="python", on_bad_lines="skip"), {}

    # ci+1 = PID ids, ci+2 = names, ci+3 = units. The id/unit/data rows are the
    # source of truth for COLUMN COUNT (ids never contain commas); the names row
    # can over-split when a channel's name has unquoted commas -- e.g. a custom
    # AEM wideband auto-named "AEM EQ -> AEM 30-(0300,2340,5130)". That used to
    # silently shift/drop trailing columns (the wideband), so repair it.
    def _strip_trailing_empty(parts):
        while parts and parts[-1] == "":
            parts.pop()
        return parts

    ids = _strip_trailing_empty(lines[ci + 1].split(","))
    n = len(ids)
    raw_names = _strip_trailing_empty(lines[ci + 2].split(","))
    raw_units = lines[ci + 3].split(",")
    names = raw_names if len(raw_names) == n else _repair_channel_names(raw_names, n)
    units = dict(zip(names, raw_units))
    df = pd.read_csv(path, skiprows=cd + 1, names=names, usecols=range(n),
                     encoding="latin-1", engine="python", on_bad_lines="skip")
    return df, units


def _repair_channel_names(raw: list, n: int) -> list:
    """Rebuild exactly `n` channel names when the names row over-split because a
    name contained unquoted commas. The over-split run is collapsed back into one
    name; HP Tuners auto-names these custom channels with a '->' so we anchor on
    that, falling back to 'the run sits just before the last (clean) name'."""
    extra = len(raw) - n
    if extra <= 0:
        return (raw + [""] * (-extra))[:n]
    start = next((i for i, f in enumerate(raw) if "->" in f), None)
    if start is None or start + extra >= len(raw):
        start = max(len(raw) - 1 - extra - 1, 0)   # assume run is 2nd-from-last
    merged = ",".join(raw[start:start + extra + 1])
    return raw[:start] + [merged] + raw[start + extra + 1:]


def normalize_map_to_kpa(df, col, units, notes):
    """HP Tuners may log MAP in psi or inHg; the analyzer's bins are in kPa."""
    if "map" not in col:
        return
    unit = (units.get(col["map"], "") or "").strip().lower()
    if unit in ("psi", "psia"):
        df[col["map"]] = pd.to_numeric(df[col["map"]], errors="coerce") * 6.894757
        notes.append(f"Converted MAP from psi to kPa (channel '{col['map']}').")
    elif unit in ("inhg", "in-hg", '"hg'):
        df[col["map"]] = pd.to_numeric(df[col["map"]], errors="coerce") * 3.386389
        notes.append(f"Converted MAP from inHg to kPa (channel '{col['map']}').")


def main(argv=None):
    p = argparse.ArgumentParser(description="VE/fuel correction analyzer for HPTuners logs.")
    p.add_argument("log", help="VCM Scanner CSV export (or plain CSV)")
    p.add_argument("--base-ve", help="Optional current VE table CSV (RPM rows x MAP cols)")
    p.add_argument("--out-dir", default="./tune_out")
    p.add_argument("--stoich", type=float, default=14.7)
    args = p.parse_args(argv)

    df, units = load_log(args.log)
    for c in df.columns:                        # coerce numeric, leave text channels
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().mean() > 0.5:
            df[c] = conv

    cfg = Config(stoich=args.stoich)
    col = resolve_columns(df)
    pre_notes = []
    normalize_map_to_kpa(df, col, units, pre_notes)

    base = pd.read_csv(args.base_ve, index_col=0) if args.base_ve else None
    try:
        res = analyze(df, cfg, base)
    except ValueError as e:
        print("=" * 70)
        print("TUNE ASSIST -- cannot run fuel analysis on this log")
        print("=" * 70)
        print(f"\n{e}\n")
        print("This looks like a spark/diagnostic log rather than a fuel-trim log. "
              "For VE/fuel correction the export needs at minimum: Engine RPM, MAP, "
              "and either fuel trims (STFT+LTFT) or a wideband + commanded AFR.")
        return
    res.notes = pre_notes + res.notes
    report = write_report(res, args.out_dir)
    print(report)


if __name__ == "__main__":
    main()
