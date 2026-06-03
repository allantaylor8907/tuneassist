"""
holley.py -- Holley EFI (Terminator X, Sniper) ingest + correction.

WHY HOLLEY IS DIFFERENT (and easier) THAN THE GM/HPTUNERS PATH
-------------------------------------------------------------
On the GM side the wideband (Allan's AEM) is invisible to the ECM, so we derive
the airflow error from the ECM's narrowband fuel trims and only use the wideband
for WOT and as a sanity check. Holley is the opposite: the wideband is built in
and *is* the control sensor. The ECU continuously compares actual AFR to a
Target AFR table and writes corrections into a Closed-Loop Compensation value
and a persistent Learn table. So on Holley the correction we'd recommend is
already being computed by the ECU -- we read it per cell rather than inferring
it.

Two equivalent ways to get the base-fuel/VE correction per cell, both supported:
  A) Direct error:     correction = Target_AFR / Actual_AFR
                        (actual leaner than target => needs more fuel => raise base fuel)
  B) Learned value:    correction = 1 + (CL_Comp + Learn)/100
                        (what the ECU already had to add/remove to hit target)
Method B is preferred when the Learn table has converged (steady, warm, lots of
samples) because it's already filtered by the ECU; Method A is better for
transient-light direct reads and for cells the Learn hasn't reached. The engine
below computes both and flags disagreement.

NATIVE .dl FILES: same limitation as HPTuners .hpl -- the binary log does not
carry readable channel names (only the "TSLCD x Logfile Terminator X" header and
the .terx tune name are readable). So we ingest the CSV the Holley EFI software
exports, not the raw .dl. The channel patterns below match Holley's CSV labels.
"""

from __future__ import annotations
import re
import numpy as np
import pandas as pd

# Holley CSV channel labels vary a little across Sniper / Terminator X / HP /
# Dominator and firmware versions; matched fuzzily like the GM side.
# NOTE on ordering: patterns are tried in order and the FIRST matching column
# (in file order) wins, so exact "^map$"/"^tps$" come first -- otherwise the
# rate-of-change columns ("MAP RoC", "TPS RoC", which appear earlier in a
# Terminator X export) would shadow the real channel.
HOLLEY_PATTERNS = {
    "time":      [r"^time", r"\brtc\b", r"elapsed", r"^sec"],
    "rpm":       [r"^rpm$", r"\brpm\b", r"engine speed"],
    "map":       [r"^map$", r"manifold", r"\bmap\b(?!.*roc)", r"\bkpa\b"],
    "tps":       [r"^tps$", r"\btps\b(?!.*roc)", r"throttle"],
    "afr_actual":[r"afr left", r"afr right", r"afr average", r"^afr$", r"^afr\b",
                  r"wideband", r"\bwbo2\b"],
    "afr_target":[r"target afr", r"afr target", r"tgt afr"],
    "cl_comp":   [r"cl comp", r"closed.?loop comp", r"clc\b"],
    "learn":     [r"current learn", r"\blearn\b(?!.*status)", r"self.?tune",
                  r"base fuel learn"],
    "cts":       [r"\bcts\b", r"coolant temp", r"coolant"],
    "mat":       [r"\bmat\b", r"\biat\b", r"air temp(?!.*enr)", r"charge temp"],
    "ign":       [r"ign(ition)? timing", r"timing", r"spark"],
    "knock":     [r"knock retard", r"\bknock\b(?!.*level)"],
    "injpw":     [r"inj.*pw", r"pulse.?width", r"\bpw\b"],
    "duty":      [r"duty"],
}


def resolve_holley(df: pd.DataFrame) -> dict:
    resolved, cols = {}, list(df.columns)
    low = {c: str(c).lower() for c in cols}
    for canon, pats in HOLLEY_PATTERNS.items():
        for p in pats:
            hit = next((c for c in cols if re.search(p, low[c])), None)
            if hit:
                resolved[canon] = hit
                break
    return resolved


def load_holley_csv(path: str) -> pd.DataFrame:
    """Holley EFI CSV export (Terminator X / Sniper / HP / Dominator). The export
    has a channel-name row, then a UNITS row (e.g. '[RPM],[kPa],[°F]'), then data.
    Some firmwares prepend metadata lines, so we sniff for the names row, skip the
    units row, and read latin-1 (the units row carries a degree sign)."""
    raw = open(path, encoding="latin-1").read().splitlines()
    hdr = 0
    for i, line in enumerate(raw[:20]):
        if re.search(r"\brpm\b", line, re.I) and line.count(",") >= 3:
            hdr = i
            break
    # The row right after the names is a units row if it's bracketed/non-numeric.
    skip = [hdr + 1] if (hdr + 1 < len(raw) and "[" in raw[hdr + 1]) else None
    df = pd.read_csv(path, header=hdr, skiprows=skip, encoding="latin-1",
                     engine="python", on_bad_lines="skip")
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().mean() > 0.5:
            df[c] = conv
    return df


def analyze_holley(df: pd.DataFrame, cfg, rpm_bins=None, map_bins=None):
    """Return (correction_grid, counts, method_disagreement_grid, notes)."""
    rpm_bins = rpm_bins or cfg.rpm_bins
    map_bins = map_bins or cfg.map_bins
    col = resolve_holley(df)
    notes = [f"Holley channels resolved: {', '.join(sorted(col)) or 'NONE'}"]
    for req in ("rpm", "map"):
        if req not in col:
            raise ValueError(f"Required Holley channel '{req}' not found. "
                             f"Headers: {list(df.columns)}")

    w = df.copy()
    w["_rpm_bin"] = pd.cut(w[col["rpm"]], bins=rpm_bins)
    w["_map_bin"] = pd.cut(w[col["map"]], bins=map_bins)

    # Filter: warm + not hard transient (if channels exist).
    if "cts" in col:
        w = w[pd.to_numeric(w[col["cts"]], errors="coerce") >= cfg.ect_min_f]
    if "tps" in col:
        rate = pd.to_numeric(df[col["tps"]], errors="coerce").diff().abs().reindex(w.index).fillna(0)
        w = w[rate <= cfg.tps_rate_max]
    if len(w) == 0:
        notes.append("RESULT: no warm/steady samples (engine never reached temp?).")
        empty = pd.DataFrame()
        return empty, empty, empty, notes

    # Method A: target / actual.
    corr_a = None
    if "afr_target" in col and "afr_actual" in col:
        tgt = pd.to_numeric(w[col["afr_target"]], errors="coerce")
        act = pd.to_numeric(w[col["afr_actual"]], errors="coerce")
        w["_corrA"] = (tgt / act).where(act > 5)
    # Method B: learned + closed-loop comp.
    if "cl_comp" in col or "learn" in col:
        comp = pd.to_numeric(w[col["cl_comp"]], errors="coerce").fillna(0) if "cl_comp" in col else 0
        learn = pd.to_numeric(w[col["learn"]], errors="coerce").fillna(0) if "learn" in col else 0
        w["_corrB"] = 1.0 + (comp + learn) / 100.0

    if "_corrA" not in w and "_corrB" not in w:
        notes.append("RESULT: log has neither Target+Actual AFR nor CL Comp/Learn -- "
                     "nothing to derive a correction from.")
        empty = pd.DataFrame()
        return empty, empty, empty, notes

    primary = "_corrB" if "_corrB" in w else "_corrA"
    notes.append(f"Primary correction method: "
                 f"{'Learn+CLcomp' if primary=='_corrB' else 'Target/Actual'}.")

    def tmean(s):
        s = s.dropna()
        if len(s) >= 5:
            lo, hi = s.quantile(0.1), s.quantile(0.9)
            s = s[(s >= lo) & (s <= hi)]
        return float(s.mean()) if len(s) else np.nan

    g = w.groupby(["_rpm_bin", "_map_bin"], observed=False)
    raw = g[primary].apply(tmean).unstack()
    counts = g.size().unstack().fillna(0).astype(int)
    correction = (1.0 + (raw - 1.0) * cfg.damping).where(counts >= cfg.min_samples).round(4)

    # Cross-check the two methods where both exist.
    disagree = pd.DataFrame()
    if "_corrA" in w and "_corrB" in w:
        a = g["_corrA"].apply(tmean).unstack()
        b = g["_corrB"].apply(tmean).unstack()
        disagree = ((a - b).abs() * 100).where(counts >= cfg.min_samples).round(1)
        notes.append("Method A/B disagreement grid computed (large values => Learn "
                     "hasn't converged in that cell; trust the direct Target/Actual read).")
    return correction, counts, disagree, notes
