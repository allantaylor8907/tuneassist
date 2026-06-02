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
    action: pd.DataFrame | None = None    # PULL / ADD / LEAN / HOT / OK
    samples: pd.DataFrame | None = None
    knock_cells: int = 0
    advisory: str = ""
    pullback: list = field(default_factory=list)  # "pull timing when..." checklist
    notes: list = field(default_factory=list)


def analyze_spark(df: pd.DataFrame, cfg, find_power: bool = False,
                  col: dict | None = None, profile=None) -> SparkResult:
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

    idx, cols = knock.index, knock.columns
    change = pd.DataFrame(np.nan, index=idx, columns=cols)
    action = pd.DataFrame(np.nan, index=idx, columns=cols, dtype=object)
    conf = counts >= cfg.min_samples

    knock_cells = 0
    for r in idx:
        load = getattr(r, "mid", None)  # placeholder; load comes from columns
        for c in cols:
            if not conf.loc[r, c]:
                continue
            kn = knock.loc[r, c]
            map_mid = float(getattr(c, "mid", np.nan))
            hot = (iat is not None and not pd.isna(iat.loc[r, c])
                   and iat.loc[r, c] > cfg.iat_spark_safe)
            lean = (afr is not None and not pd.isna(afr.loc[r, c])
                    and afr.loc[r, c] > cfg.lean_afr_flag and map_mid >= cfg.wot_map_min)

            if not pd.isna(kn) and kn > cfg.knock_pull_deg:
                knock_cells += 1
                change.loc[r, c] = -round(kn + cfg.knock_pull_margin, 1)
                if lean:
                    action.loc[r, c] = "LEAN"      # fix fuel before pulling timing
                elif hot:
                    action.loc[r, c] = "HOT"       # cool the charge / IAT comp
                else:
                    action.loc[r, c] = "PULL"
            elif find_power and map_mid >= cfg.wot_map_min and not hot and not lean:
                change.loc[r, c] = round(min(cfg.spark_add_step, cfg.spark_add_max), 1)
                action.loc[r, c] = "ADD"
            else:
                change.loc[r, c] = 0.0
                action.loc[r, c] = "OK"

    if knock_cells:
        notes.append(f"{knock_cells} cell(s) showed knock retard -- pulls include a "
                     f"+{cfg.knock_pull_margin:g} safety margin beyond the observed retard.")
    if find_power:
        notes.append(f"'Find power' on: suggesting tiny +{cfg.spark_add_step:g} adds only "
                     "in the power region where IAT and AFR are safe. Add, re-log, repeat; "
                     f"back off {cfg.spark_back_off:g} once torque flattens or knock shows.")
    else:
        notes.append("'Find power' off: only knock-driven PULLS are recommended (safe default).")

    from .profile import spark_guidance
    advisory, pullback = spark_guidance(profile, cfg.stoich)
    return SparkResult(True, "", change.round(1), action, counts,
                       knock_cells, advisory, pullback, notes)
