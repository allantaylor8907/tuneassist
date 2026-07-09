"""
spark.py -- timing analysis (DESIGN.md S10). Knock-governed and safety-first.

Reads a WOT/loaded log and recommends, per RPM x MAP cell, how many degrees of
spark to PULL (where knock shows) or, only if the user opts into "find power",
a small ADD where there's headroom. It refuses to recommend ANYTHING without a
logged knock-retard channel -- no feedback, no spark advice.

Recommendation-only, like the rest of the tool: you apply changes to the
high-octane spark table by hand and re-log.
"""

from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .engine_gm import resolve_columns


@dataclass
class SparkResult:
    can_run: bool
    reason: str = ""
    change: pd.DataFrame | None = None    # degrees to change per cell (- pull / + add)
    action: pd.DataFrame | None = None    # PULL / ADD / LEAN / HOT / OK / AT_CEILING
    samples: pd.DataFrame | None = None
    knock_cells: int = 0
    advisory: str = ""
    pullback: list = field(default_factory=list)  # "pull timing when..." checklist
    notes: list = field(default_factory=list)
    # table-aware extras (only when the user pasted their spark table w/ values)
    current: pd.DataFrame | None = None   # their table's value per analysis cell
    target: pd.DataFrame | None = None    # current + change (the number to type in)
    table_findings: list = field(default_factory=list)  # sanity checks on the table
    find_power: bool = False              # was the opt-in ADD mode on for this run


def _table_lookup(spark_table: dict):
    """Map the (sorted) analysis-bin position -> the pasted table's cell value.
    Bins come from _axis_edges(sorted(breakpoints)), so sorted-axis position i
    corresponds 1:1 to sorted breakpoint i; the values matrix is in the USER'S
    paste order (map rows x rpm cols)."""
    rpm_user, map_user = spark_table["rpm"], spark_table["map"]
    vals = spark_table["values"]
    rpm_sorted, map_sorted = sorted(set(rpm_user)), sorted(set(map_user))

    def value(ri: int, ci: int):
        try:
            r_user = rpm_user.index(rpm_sorted[ri])
            c_user = map_user.index(map_sorted[ci])
            return float(vals[c_user][r_user])
        except (IndexError, ValueError):
            return None
    return value, rpm_sorted, map_sorted


def scan_spark_table(spark_table: dict, cfg, profile=None, cam_points=None) -> list:
    """Sanity-check the pasted spark table itself (no log needed): WOT cells
    above the build's advisory ceiling, and a cam-aware idle-region check (big
    cams idle smoother with MORE advance -- the classic 'lower spark at idle'
    rule is about stability, and a lumpy cam usually wants 26-30, not 16)."""
    from .profile import spark_bounds
    findings = []
    vals, rpm_bps, map_bps = spark_table["values"], spark_table["rpm"], spark_table["map"]
    if not vals:
        return findings

    lo, hi = spark_bounds(profile, cfg.stoich)
    # WOT region = the highest-MAP column(s) at mid/high RPM
    hot_cells = []
    for mi, mp in enumerate(map_bps):
        if mp < cfg.wot_map_min:
            continue
        for ri, rpm in enumerate(rpm_bps):
            if rpm < 2400:
                continue
            v = vals[mi][ri]
            if v > hi + 1.0:
                hot_cells.append((rpm, mp, v))
    if hot_cells:
        worst = max(hot_cells, key=lambda t: t[2])
        findings.append(
            f"{len(hot_cells)} WOT cell(s) sit ABOVE the ~{lo}-{hi} deg advisory ceiling "
            f"for this build (worst: {worst[2]:g} deg at {worst[0]:g} rpm / {worst[1]:g} kPa). "
            "That may be fine on a proven combo, but it's past the typical knock-safe "
            "window -- verify with knock logging before leaning on it.")

    # idle region vs cam class: idle columns are LOW RPM; use the cam guidance.
    tips = getattr(cam_points, "idle_timing_deg", None) if cam_points else None
    if tips:
        lo_i, hi_i = tips
        idle_vals = [vals[mi][ri]
                     for mi, mp in enumerate(map_bps) if mp <= 60
                     for ri, rpm in enumerate(rpm_bps) if 500 <= rpm <= 1200]
        if idle_vals:
            avg = sum(idle_vals) / len(idle_vals)
            if avg < lo_i - 3:
                findings.append(
                    f"Idle-area spark averages ~{avg:.0f} deg but this cam class idles "
                    f"best around {lo_i}-{hi_i} deg -- a lumpy cam wants MORE advance at "
                    "idle (it smooths the lope and props up idle vacuum), then normal "
                    "values as RPM climbs.")
            elif avg > hi_i + 4:
                findings.append(
                    f"Idle-area spark averages ~{avg:.0f} deg -- above the ~{lo_i}-{hi_i} "
                    "deg this cam class usually likes; if idle hunts, try stepping it down.")
    return findings


def analyze_spark(df: pd.DataFrame, cfg, find_power: bool = False,
                  col: dict | None = None, profile=None,
                  spark_table: dict | None = None, cam_points=None) -> SparkResult:
    col = col or resolve_columns(df)
    notes = []

    for req in ("rpm", "map"):
        if req not in col:
            return SparkResult(False, f"No '{req}' channel -- can't bin spark by cell.")
    if "knock" not in col:
        return SparkResult(
            False,
            "No knock-retard channel in this log. Spark work is knock-governed: "
            "without knock feedback the tool will not recommend timing changes. "
            "Log knock retard (and ideally a wideband) and re-capture a WOT pull.",
            notes=["Tip: most no-start/cruise logs omit knock -- enable it for spark work."])

    w = df.copy()
    # Warm, non-transient, actually running.
    if "ect" in col:
        w = w[pd.to_numeric(w[col["ect"]], errors="coerce") >= cfg.ect_min_f]
    if "rpm" in col:
        w = w[pd.to_numeric(w[col["rpm"]], errors="coerce") > 400]
    if len(w) < cfg.min_samples:
        return SparkResult(False, "Too few warm/running samples for spark analysis.")

    w["_rpm_bin"] = pd.cut(pd.to_numeric(w[col["rpm"]], errors="coerce"), bins=cfg.rpm_bins)
    w["_map_bin"] = pd.cut(pd.to_numeric(w[col["map"]], errors="coerce"), bins=cfg.map_bins)
    keys = ["_rpm_bin", "_map_bin"]
    g = w.groupby(keys, observed=False)

    def grid(series):
        return g[series].mean().unstack()

    knock = grid(col["knock"])
    counts = g.size().unstack().fillna(0).astype(int)
    iat = grid(col["iat"]) if "iat" in col else None
    afr = grid(col["afr_actual"]) if "afr_actual" in col else None

    delivered = grid(col["spark"]) if "spark" in col else None

    idx, cols = knock.index, knock.columns
    change = pd.DataFrame(np.nan, index=idx, columns=cols)
    action = pd.DataFrame(np.nan, index=idx, columns=cols, dtype=object)
    conf = counts >= cfg.min_samples

    # table-aware mode: the pasted spark table gives the CURRENT value per cell,
    # so recommendations become absolute (24 -> 25) and ADDs cap at the build's
    # advisory ceiling instead of creeping forever. Positions align only when
    # the binning axes came from this same table (core derives them so).
    lookup = None
    current = target = None
    ceiling = None
    if spark_table and spark_table.get("values"):
        val_at, rpm_sorted, map_sorted = _table_lookup(spark_table)
        if len(idx) == len(rpm_sorted) and len(cols) == len(map_sorted):
            lookup = val_at
            current = pd.DataFrame(np.nan, index=idx, columns=cols)
            target = pd.DataFrame(np.nan, index=idx, columns=cols)
            from .profile import spark_bounds
            ceiling = spark_bounds(profile, cfg.stoich)[1]

    knock_cells = 0
    deficit_cells = []
    for ri, r in enumerate(idx):
        for ci, c in enumerate(cols):
            if not conf.loc[r, c]:
                continue
            kn = knock.loc[r, c]
            map_mid = float(getattr(c, "mid", np.nan))
            hot = (iat is not None and not pd.isna(iat.loc[r, c])
                   and iat.loc[r, c] > cfg.iat_spark_safe)
            lean = (afr is not None and not pd.isna(afr.loc[r, c])
                    and afr.loc[r, c] > cfg.lean_afr_flag and map_mid >= cfg.wot_map_min)
            cur = lookup(ri, ci) if lookup else None
            if current is not None and cur is not None:
                current.loc[r, c] = cur
                # where did the logged advance actually land vs the table?
                if delivered is not None and not pd.isna(delivered.loc[r, c]):
                    gap = cur - float(delivered.loc[r, c])
                    if gap > 2.5:
                        deficit_cells.append((getattr(r, "left", r), map_mid, gap))

            if not pd.isna(kn) and kn > cfg.knock_pull_deg:
                knock_cells += 1
                change.loc[r, c] = -round(kn + cfg.knock_pull_margin, 1)
                if lean:
                    action.loc[r, c] = "LEAN"      # fix fuel before pulling timing
                elif hot:
                    action.loc[r, c] = "HOT"       # cool the charge / IAT comp
                else:
                    action.loc[r, c] = "PULL"
            elif map_mid >= cfg.wot_map_min and not hot and not lean:
                # Power ADDs are ALWAYS computed (the GUI decides whether to show
                # them; `find_power` is just the default reveal). Safe region only:
                # power load, no knock, AFR/IAT ok.
                add = min(cfg.spark_add_step, cfg.spark_add_max)
                if cur is not None and ceiling is not None:
                    room = ceiling - cur
                    if room <= 0.2:                # already at/over the sanity window
                        change.loc[r, c] = 0.0
                        action.loc[r, c] = "AT_CEILING"
                        if target is not None and cur is not None:
                            target.loc[r, c] = cur
                        continue
                    add = min(add, room)
                change.loc[r, c] = round(add, 1)
                action.loc[r, c] = "ADD"
            else:
                change.loc[r, c] = 0.0
                action.loc[r, c] = "OK"
            if target is not None and cur is not None and not pd.isna(change.loc[r, c]):
                target.loc[r, c] = round(cur + float(change.loc[r, c]), 1)

    add_cells = int((action.stack().astype(str).isin(["ADD", "AT_CEILING"])).sum()) \
        if action is not None else 0
    if knock_cells:
        notes.append(f"{knock_cells} cell(s) showed knock retard -- pulls include a "
                     f"+{cfg.knock_pull_margin:g} safety margin beyond the observed retard.")
    if add_cells:
        notes.append(f"{add_cells} power cell(s) look safe for a small +{cfg.spark_add_step:g} deg "
                     "add (power load, no knock, AFR/IAT ok). Add, re-log, repeat; back off "
                     f"{cfg.spark_back_off:g} once torque flattens or knock shows. (Adds are hidden "
                     "until you flip 'Add power' -- pulling timing is always the safe default.)")
    if lookup and ceiling is not None:
        notes.append(f"Your spark table is loaded: recommendations are absolute (current -> "
                     f"target), and ADDs stop at the ~{ceiling:g} deg advisory ceiling for "
                     "this build.")
    if deficit_cells:
        worst = max(deficit_cells, key=lambda t: t[2])
        notes.append(f"{len(deficit_cells)} cell(s) DELIVERED less timing than your table "
                     f"commands (worst: -{worst[2]:.1f} deg near {worst[0]:g} rpm / "
                     f"{worst[1]:g} kPa) -- knock, IAT, or torque management is pulling it; "
                     "see the timing-below-commanded finding for which.")

    table_findings = []
    if spark_table and spark_table.get("values"):
        try:
            table_findings = scan_spark_table(spark_table, cfg, profile, cam_points)
        except Exception:                       # pragma: no cover - defensive
            pass

    from .profile import spark_guidance
    advisory, pullback = spark_guidance(profile, cfg.stoich)
    return SparkResult(True, "", change.round(1), action, counts,
                       knock_cells, advisory, pullback, notes,
                       current=current.round(1) if current is not None else None,
                       target=target if target is not None else None,
                       table_findings=table_findings, find_power=find_power)
