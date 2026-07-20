"""
core.py -- the headless analysis engine, with NO terminal/UI dependency.

This is the decoupling boundary discussed in the architecture review: everything
that turns a log + setup into a structured result lives here; the wizard/render
layer (and any future UI -- Textual today, a Rust/Go front-end tomorrow) is just
a consumer of `analyze_log()`. `CoreResult.to_dict()` is the stable JSON contract
and the regression oracle a future port must reproduce.

No `rich`, no `input()`, no prints. Pure data in, structured data out.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field

import pandas as pd

from .engine_gm import (Config, load_log, resolve_columns, normalize_map_to_kpa,
                        analyze, maf_correction, write_report)
from .triage import triage
from . import holley
from . import stages
from . import diagnostics
from .spark import analyze_spark


# --------------------------------------------------------------------------
# Session options (hardware/fuel/intent). Plain data; lives here so both the
# UI layer and the headless core can share it without an import cycle.
# --------------------------------------------------------------------------
@dataclass
class SessionOpts:
    cfg: Config
    airflow_mode: str = "ve_sd"     # 've_sd' | 'maf' | 'no_maf'
    tune_spark: bool = False
    find_power: bool = False
    cam_points: object = None       # cams.CamStartingPoints
    cam_spec: object = None         # raw cams.CamSpec (round-trips to disk)
    profile: object = None          # profile.EngineProfile
    # Engine OEM + architecture axes (see docs/PLATFORMS.md). Independent of the
    # `platform` (tuning software). Auto-derived/detected when left as None.
    make: str | None = None         # 'gm' | 'ford' | 'mopar' ...
    architecture: str | None = None  # 'gm_gen3_4_ls' | 'ford_coyote' ...
    # The vehicle's REAL table breakpoints, so the correction grids match the
    # user's tables cell-for-cell (their tuning software rarely has the same axes
    # as our defaults). {"rpm": [...], "map": [...]} or None = defaults. VE and
    # spark are separate tables with separate axes.
    ve_axes: dict | None = None
    spark_axes: dict | None = None
    # Full pasted tune tables (axes + cell values + pasted timestamp), keyed
    # ve/spark/maf -- see clean_tables(). Values unlock the table-aware features
    # (absolute spark targets, ceiling caps, table sanity checks); when present,
    # the axes above are derived from them automatically.
    tables: dict | None = None


def opts_to_record(platform: str, opts: "SessionOpts") -> dict:
    """Serialize a SessionOpts into a JSON-friendly vehicle record (garage)."""
    cam = opts.cam_spec
    prof = opts.profile
    return {
        "platform": platform,
        "make": opts.make,
        "architecture": opts.architecture,
        "stoich": opts.cfg.stoich,
        "airflow_mode": opts.airflow_mode,
        "tune_spark": opts.tune_spark,
        "find_power": opts.find_power,
        "ve_axes": clean_ve_axes(opts.ve_axes),
        "spark_axes": clean_ve_axes(opts.spark_axes),
        "tables": clean_tables(opts.tables),
        "cam": (None if cam is None else {
            "intake_dur_050": cam.intake_dur_050, "exhaust_dur_050": cam.exhaust_dur_050,
            "lsa": cam.lsa, "lift": cam.lift}),
        "profile": (None if prof is None else {
            "block": prof.block, "compression": prof.compression,
            "displacement": prof.displacement, "power_adder": prof.power_adder,
            "engine": getattr(prof, "engine", None),
            "mods": list(getattr(prof, "mods", []) or [])}),
    }


def record_to_opts(record: dict):
    """Rebuild (platform, SessionOpts) from a stored vehicle record."""
    from . import cams
    from .profile import EngineProfile
    cfg = Config()
    cfg.stoich = record.get("stoich", 14.7)
    plat = record.get("platform", "gm")
    d_make, d_arch = default_make_arch(plat)
    opts = SessionOpts(cfg=cfg, airflow_mode=record.get("airflow_mode", "ve_sd"),
                       tune_spark=record.get("tune_spark", False),
                       find_power=record.get("find_power", False),
                       make=record.get("make") or d_make,
                       architecture=record.get("architecture") or d_arch,
                       ve_axes=clean_ve_axes(record.get("ve_axes")),
                       spark_axes=clean_ve_axes(record.get("spark_axes")),
                       tables=clean_tables(record.get("tables")))
    cam = record.get("cam")
    if cam:
        opts.cam_spec = cams.CamSpec(**cam)
        opts.cam_points = cams.starting_points(opts.cam_spec)
    prof = record.get("profile")
    if prof:
        opts.profile = EngineProfile(**prof)
    return record.get("platform", "gm"), opts


class _HolleyResult:
    """Adapter so a Holley correction grid quacks like an engine_gm.Result."""
    def __init__(self, correction, samples, notes):
        self.correction = correction
        self.samples = samples
        self.notes = notes
        self.recommendation = None
        self.wb_dev = None
        self.safety = []


# --------------------------------------------------------------------------
# Ingest / platform detection (pure)
# --------------------------------------------------------------------------
def detect_platform(path: str) -> str:
    try:
        head = open(path, encoding="latin-1").read(4000).lower()
    except OSError:
        head = ""
    if "hp tuners" in head or "vcm" in head:
        return "gm"
    if any(k in head for k in ("holley", "terminator", "sniper", "tslcd")):
        return "holley"
    if "target afr" in head or "cl comp" in head:
        return "holley"
    return "gm"


# --- platform / make / architecture model (docs/PLATFORMS.md) -----------------
# NOTE: the internal `platform` value stays "gm"/"holley" for back-compat with
# the JSON contract and on-disk garages; "gm" is the HP Tuners platform legacy
# value. The display label and the make/architecture axes are the new model.
_PLATFORM_LABELS = {"holley": "Holley EFI", "hptuners": "HP Tuners", "gm": "HP Tuners"}


def platform_label(platform: str) -> str:
    """Human name for the tuning platform (software/ECU). 'gm' is the legacy value
    for HP Tuners."""
    return _PLATFORM_LABELS.get(platform, "HP Tuners")


# Engine architectures (where the airflow/spark strategy lives). See
# docs/TUNING_BY_PLATFORM.md for how each is tuned, and fitment.py for which
# (platform, make, generation, engine) combinations are real.
ARCHITECTURES = {
    "gm_gen3_ls": "GM Gen 3 LS (VE table + MAF)",
    "gm_gen4_ls": "GM Gen 4 LS (Virtual VE / MAF-only)",
    "gm_gen5_lt": "GM Gen 5 LT (direct injection)",
    "ford_modular": "Ford Modular 4.6/5.4",
    "ford_coyote": "Ford Coyote (MAF / load %)",
    "ford_godzilla": "Ford Godzilla 7.3",
    "mopar_hemi": "Mopar Gen 3 HEMI",
    # Holley: the product IS the architecture (self-learn strategy throughout).
    "holley_sniper": "Holley Sniper (TBI self-learn)",
    "holley_terminator": "Holley Terminator X",
    "holley_hp": "Holley HP EFI",
    "holley_dominator": "Holley Dominator",
    "holley_selflearn": "Holley self-learning",     # legacy records
    "generic": "Generic / other",
}


def detect_holley_product(path: str) -> str:
    """Best-effort Holley product from the log header text (the same banner
    detect_platform sniffs). Falls back to the generic self-learn key."""
    try:
        head = open(path, encoding="latin-1").read(4000).lower()
    except OSError:
        head = ""
    if "sniper" in head:
        return "holley_sniper"
    if "terminator" in head:
        return "holley_terminator"
    if "dominator" in head:
        return "holley_dominator"
    return "holley_selflearn"


def default_make_arch(platform: str) -> tuple[str, str]:
    """Sensible (make, architecture) when none was chosen/detected. Gen 3 LS is
    the most common swap, so it's the GM default."""
    if platform == "holley":
        return "gm", "holley_selflearn"
    return "gm", "gm_gen3_ls"


def detect_make(df) -> str | None:
    """Best-effort engine make from the channel set. The reliable tell is the
    manifold-MAP channel: GM speed-density logs carry it; Ford/OBD-II logs don't
    (they run on Absolute Load %). 'WB EQ Ratio' and 'Absolute Load' appear on
    BOTH (GM logs them too), so neither can flag Ford on its own. Default GM."""
    cols = " ".join(str(c).lower() for c in df.columns)
    if "manifold absolute pressure" in cols:
        return "gm"
    if "coyote" in cols:
        return "ford"
    # No manifold MAP + the OBD-II load/lambda signature -> Ford/OBD-II.
    if "absolute load" in cols or "wb eq ratio" in cols:
        return "ford"
    return "gm"


def detect_architecture(df, make: str, platform: str) -> str:
    """Best-effort engine family from the channel fingerprints:
    DI high-pressure rail -> Gen 5 LT; VVT cam-phaser channels -> Gen 4 LS;
    otherwise Gen 3 LS. Non-GM/Holley short-circuit."""
    if platform == "holley":
        return "holley_selflearn"
    if make == "ford":
        return "ford_coyote"
    cols = " ".join(str(c).lower() for c in df.columns)
    if any(k in cols for k in ("rail pressure", "hpfp", "high pressure fuel", "direct inj")):
        return "gm_gen5_lt"
    if any(k in cols for k in ("cam angle", "cam error", "cam phaser", "intake cam",
                               "exhaust cam", "vvt")):
        return "gm_gen4_ls"
    # Torque-based ECM (a driver-demand torque request alongside dynamic airflow /
    # TCC PWM control) is Gen 4 -- Gen 3 P01/P59 is MAP/MAF-based, not torque-based.
    if (any(k in cols for k in ("dynamic airflow", "tcc pwm")) and
            any(k in cols for k in ("desired engine torque", "torque mgt",
                                    "driver demand", "tcs desired"))):
        return "gm_gen4_ls"
    return "gm_gen3_ls"


def stoich_from_ethanol(pct: float) -> float:
    """Stoichiometric AFR for a given ethanol content %. Linear from E0 (14.64)
    to E100 (~9.0): E10~14.1, E85~9.85."""
    pct = max(0.0, min(100.0, float(pct)))
    return round(14.64 - (pct / 100.0) * (14.64 - 9.0), 2)


def resolve_for(df: pd.DataFrame, platform: str) -> dict:
    return holley.resolve_holley(df) if platform == "holley" else resolve_columns(df)


def ingest(path: str, platform: str, cfg: Config):
    """Return (df, col) for the chosen platform."""
    if platform == "holley":
        df = holley.load_holley_csv(path)
        return df, holley.resolve_holley(df)
    df, units = load_log(path)
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().mean() > 0.5:
            df[c] = conv
    col = resolve_columns(df)
    normalize_map_to_kpa(df, col, units, [])
    return df, col


def _primary_change_finding(summary, platform: str, airflow_mode: str,
                            architecture: str = "gm_gen3_ls"):
    """Turn the main fuel/VE correction into a lead diagnosis item: WHAT to change,
    WHERE (which table), and HOW (multiply-by-percent). Architecture-aware -- Gen 4
    has no VE table (it's MAF-only). None when nothing material to change."""
    from .diagnostics import Finding
    worst = getattr(summary, "max_abs_pct", 0.0)
    if getattr(summary, "n_confident", 0) == 0 or worst < 1.5:
        return None
    from .tables import table as _tbl
    n = summary.n_confident
    focus = summary.focus or "the populated cells"
    off = summary.offset or {}
    gen4 = architecture == "gm_gen4_ls"
    gen5 = architecture == "gm_gen5_lt"
    if platform == "holley":
        table, what = _tbl("holley", "ve"), "Base fuel"
    elif gen4 or airflow_mode == "maf":
        table, what = _tbl("gm", "maf"), "MAF curve"
    elif gen5:
        table, what = _tbl("gm", "maf"), "airflow (MAF)"
    else:
        table, what = _tbl("gm", "ve"), "VE table"
    sev = "warning" if worst >= 3 else "info"
    corrections = []
    if gen4:
        corrections.append(
            "GEN 4: there's no editable VE table (it's Virtual VE). Apply this onto the "
            "MAF 'Airflow vs Frequency' curve (run MAF-only: Dynamic Airflow High-RPM "
            "Disable = 0) -- do NOT paste it into a VE table.")
    elif gen5:
        corrections.append(
            "GEN 5 (DI): use this as airflow guidance for the VVE/MAF; also mind the DI "
            "fuel-system ceiling and driver-demand torque limits.")
    elif (off.get("shape") == "global_offset" and platform != "holley"
            and airflow_mode != "maf"):
        corrections.append(
            f"It's a nearly-FLAT ~{off.get('median_pct')}% offset -- a single scalar "
            "(injector flow / fuel pressure) may fix it more cleanly than reshaping cells.")
    corrections.append(
        f"Apply the correction grid (shown below) to your {table}: multiply-by-percent "
        "-- a +5 cell means multiply that cell by 1.05.")
    corrections.append("Leave cells marked '-' (too few samples); re-log to confirm they shrink.")
    return Finding("APPLY_FUEL", sev, f"{what} needs correction (apply the grid below)",
                   f"The correction grid has changes up to {worst:.0f}% across {n} cells "
                   f"(biggest in {focus}).", [], corrections, "high")


def _raises_fuel_up_top(result) -> bool:
    """True if the correction adds fuel (positive) in the upper RPM / high-load
    cells -- the signature of an airflow increase the base table doesn't model."""
    corr = getattr(result, "correction", None)
    if corr is None or corr.empty:
        return False
    vals = []
    for r in corr.index:
        for c in corr.columns:
            v = corr.loc[r, c]
            if pd.isna(v):
                continue
            rpm_mid = float(getattr(r, "mid", 0) or 0)
            map_mid = float(getattr(c, "mid", 0) or 0)
            if rpm_mid >= 2800 or map_mid >= 80:
                vals.append((v - 1.0) * 100.0)
    if len(vals) < 4:
        return False
    return (sum(vals) / len(vals)) > 1.5


def _apply_mod_insights(findings, summary, result, mods):
    """Let the checked bolt-ons explain the data: each note fires only when the
    data shows the matching pattern (DESIGN.md S13)."""
    low = " | ".join(m.lower() for m in mods)
    def has(*subs):
        return any(s in low for s in subs)
    by_id = {f.id: f for f in findings}
    off = summary.offset or {}

    apply_f = by_id.get("APPLY_FUEL")
    if apply_f is not None:
        if has("larger injector", "injector") and off.get("shape") == "global_offset":
            apply_f.corrections.insert(0,
                "You're running LARGER INJECTORS and this correction is a flat offset -- "
                "that's almost always the injector flow-rate/scaling not updated. Set the "
                "correct injector data FIRST; it likely flattens most of this.")
        if has("intake manifold") and off.get("shape") == "table_shape":
            apply_f.corrections.append(
                "Your intake-manifold swap reshapes the VE curve (new runners move where it "
                "breathes best) -- expect to reshape cells, not apply one scalar.")
        if has("ported head", "long-tube", "header", "cold-air", "throttle body") \
                and _raises_fuel_up_top(result):
            apply_f.corrections.append(
                "The raise-VE up top is EXPECTED from your airflow mods (heads/headers/intake) "
                "-- the engine flows more than the stock table models. Adding fuel there is "
                "correct calibration, not a fault.")

    bank = by_id.get("BANK_IMBALANCE")
    if bank is not None and has("long-tube", "header"):
        bank.causes.insert(0,
            "a header collector/gasket exhaust leak near one O2 (common right after install) "
            "reading false-lean on that bank")

    lean = by_id.get("LEAN_CRUISE")
    if lean is not None and has("cold-air", "intake"):
        lean.causes.append(
            "a cold-air intake / intake-tube change alters the MAF airflow signal -- the MAF "
            "curve may need recalibration (not the VE table)")


def _name_tables(findings, platform: str):
    """Append the exact vendor table to edit for findings whose fix maps to one.
    (APPLY_FUEL and STARTUP_FLARE already name their tables inline.)"""
    from .tables import table
    keys = {
        "KNOCK": "spark", "TIMING_BELOW_COMMAND": "spark", "INJ_DUTY": "injector",
        "HIGH_IAT": "iat_spark",
        "IDLE_HIGH": "idle_air", "IDLE_LOW": "idle_air", "VACUUM_LEAK": "idle_air",
        "IDLE_AIRFLOW_OFF": "idle_air",
        "IDLE_TIMING_SWING": "idle_spark", "WARMUP_RICH": "warmup_enr",
        "WARMUP_LEAN": "warmup_enr", "ENRICH_NOT_DECAYED": "ase",
        "STARTUP_SAG": "startup_air", "ROLLING_IDLE_HANG": "idle_air",
        "WOT_RICH": "pe", "WOT_TARGET_LEAN": "pe", "WOT_TARGET_RISK": "pe",
        "IDLE_RICH": "ve", "IDLE_LEAN": "ve",
    }
    for f in findings:
        k = keys.get(f.id)
        if k:
            f.corrections.append(f"In your tune: the {table(platform, k)}.")


def _annotate_safety_resolution(findings, summary, platform: str, airflow_mode: str):
    """Append a line to each fuel-safety finding stating whether applying the
    recommended fuel/VE correction resolves it, or -- if it can't (a hardware
    limit) -- what actually needs to change."""
    ids = {f.id for f in findings}
    hardware_limited = bool(ids & {"INJ_DUTY", "FUEL_PRESSURE_DROP"})
    grid = ("base fuel table" if platform == "holley"
            else "MAF curve" if airflow_mode == "maf" else "VE/fuel correction")
    for f in findings:
        if f.id not in ("WOT_SHORTFALL", "WOT_LEAN", "BOOST_LEAN"):
            continue
        if hardware_limited:
            f.corrections.append(
                f"WON'T be fixed by the {grid}: you're out of injector/fuel-pressure "
                "headroom (see the fuel-supply finding) -- fix that hardware first.")
        elif getattr(summary, "wot_covered", False):
            f.corrections.append(
                f"The {grid} below already covers these high-load cells (from the "
                "wideband) -- applying it should richen them; re-log to confirm it's safe.")
        else:
            f.corrections.append(
                "Not covered by the correction grid (no wideband data up here) -- "
                "richen the WOT commanded-AFR / power-enrichment target directly, then re-log.")


def _blocked_prescription(reason: str, cfg: Config, platform: str):
    """Tailored next-step when the log ran but yielded no usable cells."""
    reason = reason.replace("RESULT: ", "")
    cold = "operating temp" in reason
    no_map = "manifold-pressure" in reason or "load axis" in reason
    if no_map:
        action = ("Add an Intake MAP channel (kPa) -- it's the load axis. The "
                  "correction grid is RPM x MAP, so without manifold pressure there's "
                  "nothing to bin the fuel error against. Your fuel trims still read "
                  "fine (see the diagnosis), this just can't become a table yet.")
        drive = ("Re-log with Intake MAP added. NOTE: if this is a Ford / OBD-II log "
                 "(it logs Absolute Load % and a lambda wideband instead of MAP), "
                 "tuneassist targets GM speed-density + Holley -- the trim read above "
                 "applies, but the VE/MAF table guidance is GM-specific.")
    elif cold:
        action = ("Get the engine fully warmed up (coolant past "
                  f"{int(cfg.ect_min_f)} F) before logging -- cold-enrichment data "
                  "isn't valid for VE/fuel correction.")
        drive = ("Warm it to operating temp, THEN do the cruise drive. Or log a "
                 "longer session so the warm portion has enough samples.")
    else:
        action = ("This log has no usable fueling-error signal. Make sure you're "
                  "logging fuel trims (STFT+LTFT) or a wideband + commanded AFR.")
        drive = "Re-log with the fuel channels above, then a steady cruise drive."
    return stages.Prescription(
        "TUNE_VE_SD", "Log isn't usable yet", reason,
        actions=[action], drive=drive,
        capture=stages._GM_CHANNELS if platform != "holley" else
                ["RPM", "MAP", "AFR", "Target AFR", "CTS", "TPS"])


# --------------------------------------------------------------------------
# The headless result
# --------------------------------------------------------------------------
@dataclass
class CoreResult:
    platform: str
    triage: object                         # TriageResult
    stage: str
    summary: object                        # stages.AnalysisSummary
    make: str = "gm"                       # engine OEM (docs/PLATFORMS.md)
    architecture: str = "gm_gen3_4_ls"     # airflow/spark family
    result: object = None                  # engine_gm.Result | _HolleyResult | None
    spark: object = None                   # spark.SparkResult | None
    maf: tuple = (None, None, [])          # (Series|None, Series|None, notes)
    prescription: object = None            # stages.Prescription
    empty_reason: str | None = None
    findings: list = field(default_factory=list)   # diagnostics.Finding list
    notes: list = field(default_factory=list)
    timeseries: dict | None = None         # downsampled traces (GUI timeline)
    ve_axes: dict | None = None            # the custom VE table axes used, if any
    spark_axes: dict | None = None         # the custom spark table axes used, if any
    tables: dict | None = None             # the cleaned pasted tune tables, if any
    channel_coverage: dict | None = None   # logged vs missing channels for this log

    @property
    def has_grid(self) -> bool:
        c = getattr(self.result, "correction", None)
        return c is not None and not c.empty

    def to_dict(self) -> dict:
        """The stable JSON contract: a future port must reproduce this."""
        return result_to_dict(self)


def analyze_log(path: str, opts: SessionOpts, platform: str | None = None,
                out_dir: str | None = None) -> CoreResult:
    """The one entry point: log path + setup -> structured CoreResult. No UI.

    out_dir (optional) only controls whether CSV side-products get written; pass
    None for a pure headless/JSON run."""
    cfg = opts.cfg
    platform = platform or detect_platform(path)
    df, col = ingest(path, platform, cfg)

    # Make / architecture axes: respect what the user set, else detect/default.
    if opts.make is None:
        opts.make = detect_make(df)
    if opts.architecture is None:
        if platform == "holley":
            opts.architecture = detect_holley_product(path)
        else:
            opts.architecture = detect_architecture(df, opts.make, platform)

    # Ethanol auto-detect: if the log carries an ethanol-content channel and it
    # disagrees with the configured stoich, trust the measurement (flex fuel).
    eth_note = None
    if "ethanol" in col:
        e = pd.to_numeric(df[col["ethanol"]], errors="coerce").dropna()
        e = e[(e >= 0) & (e <= 100)]
        if len(e) > 20:
            pct = float(e.median())
            new_stoich = stoich_from_ethanol(pct)
            if abs(new_stoich - cfg.stoich) > 0.4:
                eth_note = (f"Ethanol ~{pct:.0f}% detected -> using stoich "
                            f"{new_stoich} (was {cfg.stoich}). Flex fuel.")
                cfg.stoich = new_stoich

    tcol = {k: col[k] for k in ("rpm", "time", "tps", "map") if k in col}
    if "ect" in col:                       # let triage tell "warm but no RPM signal"
        tcol["ect"] = col["ect"]
    elif "cts" in col:
        tcol["ect"] = col["cts"]
    tr = triage(df, tcol)

    summary = stages.AnalysisSummary()
    result = None
    spark = None
    maf = (None, None, [])
    notes: list = []
    if eth_note:
        notes.append(eth_note)

    # If the user gave their real VE/base-fuel table breakpoints, bin the
    # correction onto THOSE exact axes so the grid (and paste-ready TSV) lines up
    # cell-for-cell with the table in their tuning software. Use a copy of the cfg
    # so ONLY the fuel correction is rebinned -- spark and MAF have their own,
    # different table axes and must keep the default bins.
    tables = clean_tables(opts.tables)
    ve_axes = clean_ve_axes(opts.ve_axes) or (
        clean_ve_axes(tables.get("ve")) if tables and tables.get("ve") else None)
    spark_axes = clean_ve_axes(opts.spark_axes) or (      # spark is its own table
        clean_ve_axes(tables.get("spark")) if tables and tables.get("spark") else None)
    corr_cfg = cfg
    if ve_axes:
        import copy as _copy
        corr_cfg = _copy.copy(cfg)
        corr_cfg.rpm_bins = _axis_edges(ve_axes["rpm"])
        corr_cfg.map_bins = _axis_edges(ve_axes["map"])

    if tr.can_correct:
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        try:
            if platform == "holley":
                corr, counts, _disagree, hnotes = holley.analyze_holley(df, corr_cfg)
                result = _HolleyResult(corr, counts, hnotes)
                if out_dir and not corr.empty:
                    corr.to_csv(os.path.join(out_dir, "holley_base_fuel_correction.csv"))
            else:
                result = analyze(df, corr_cfg)
                if out_dir:
                    write_report(result, out_dir)
                if opts.airflow_mode == "maf":
                    maf = maf_correction(df, cfg)
            if ve_axes and result is not None:
                _relabel_to_breakpoints(result, ve_axes["rpm"], ve_axes["map"])
                notes.append(
                    f"Correction binned to your VE table axes "
                    f"({len(ve_axes['rpm'])} RPM x {len(ve_axes['map'])} MAP) -- the grid "
                    "and the copied TSV line up with your table cell-for-cell.")
            summary = stages.summarize(result, cfg)
        except ValueError as e:
            notes.append(f"Could not analyze: {e}")
        if opts.tune_spark:
            try:
                spark_cfg = cfg
                if spark_axes:
                    import copy as _copy
                    spark_cfg = _copy.copy(cfg)
                    spark_cfg.rpm_bins = _axis_edges(spark_axes["rpm"])
                    spark_cfg.map_bins = _axis_edges(spark_axes["map"])
                spark = analyze_spark(df, spark_cfg, find_power=opts.find_power,
                                      profile=opts.profile,
                                      spark_table=(tables or {}).get("spark"),
                                      cam_points=opts.cam_points)
                if spark_axes and spark is not None and spark.can_run:
                    _relabel_to_breakpoints(spark, spark_axes["rpm"], spark_axes["map"],
                                            names=("change", "action", "samples",
                                                   "current", "target"))
                    notes.append(
                        f"Spark grid binned to your spark table axes "
                        f"({len(spark_axes['rpm'])} RPM x {len(spark_axes['map'])} MAP) -- "
                        "the copied spark TSV lines up cell-for-cell.")
            except Exception as e:   # pragma: no cover - defensive
                notes.append(f"Spark analysis skipped: {e}")

    # Determine the journey stage first -- the diagnostics' logging coach gates
    # its channel nudges by stage, so it needs to know where the build is.
    spark_has_work = bool(spark and spark.can_run and
                          (spark.knock_cells or (spark.action is not None and
                           (spark.action.stack() == "ADD").any())))
    stage = stages.determine_stage(tr.state, summary, airflow_mode=opts.airflow_mode,
                                   tune_spark=opts.tune_spark, spark_has_work=spark_has_work)

    # Pattern-based diagnosis (symptom -> cause -> correction). Runs on any
    # running log; normalize Holley channel names to the canonical keys first.
    findings = []
    if tr.can_correct:
        dcol = dict(col)
        for canon, alias in (("afr_cmd", "afr_target"), ("ect", "cts"), ("iat", "mat")):
            if canon not in dcol and alias in col:
                dcol[canon] = col[alias]
        cam_class = getattr(opts.cam_points, "klass", None)
        try:
            findings = diagnostics.diagnose(df, dcol, cfg, platform=platform,
                                            profile=opts.profile, cam_class=cam_class,
                                            stage=stage)
        except Exception as e:   # pragma: no cover - defensive
            notes.append(f"Diagnostics skipped: {e}")
        # The main fuel/VE correction is itself a change to apply -- make it the
        # lead diagnosis item so users see "what to change" up top, not just the
        # peripheral issues.
        primary = _primary_change_finding(summary, platform, opts.airflow_mode,
                                          opts.architecture or "gm_gen3_ls")
        if primary is not None:
            rank = diagnostics.SEVERITY_RANK
            cr_ = {"high": 0, "medium": 1, "low": 2}
            findings = [primary] + findings
            findings.sort(key=lambda f: (rank.get(f.severity, 9), cr_.get(f.confidence, 9)))
        # Tell the user whether the recommended fuel/VE change resolves each
        # safety finding -- or, if it can't, what actually needs to change.
        _annotate_safety_resolution(findings, summary, platform, opts.airflow_mode)
        # Use the engine's bolt-on mods to explain the data (DESIGN.md S13).
        mods = list(getattr(opts.profile, "mods", []) or [])
        if mods:
            _apply_mod_insights(findings, summary, result, mods)
        # Point each finding at the EXACT vendor table to edit.
        _name_tables(findings, platform)
    elif tr.state in ("CRANKING_NO_START", "STARTED_STALLED"):
        # It cranked (or caught and died) but isn't tunable yet -- diagnose WHY it
        # won't start from the crank-window channels instead of leaving the user
        # with only the generic triage advice.
        try:
            from . import crank
            findings = crank.diagnose_no_start(df, col, cfg, tr.state)
        except Exception as e:   # pragma: no cover - defensive
            notes.append(f"No-start diagnosis skipped: {e}")

    if eth_note:
        findings.append(diagnostics.Finding(
            "FUEL_ETHANOL", "info", "Flex fuel (ethanol) detected", eth_note,
            ["the log's ethanol-content channel set the stoichiometric AFR"],
            ["No action -- targets/cross-checks now use the ethanol-corrected stoich. "
             "Make sure your tune's fuel/stoich matches if you change blends."], "high"))

    # If the engine ran but every sample was filtered out, explain the blocker
    # rather than giving generic "go drive" advice.
    empty_reason = None
    if tr.can_correct and summary.n_confident == 0:
        if result is not None:
            empty_reason = next((n for n in getattr(result, "notes", [])
                                 if n.startswith("RESULT")), None)
        # No manifold-pressure channel -> there's no load axis to bin a RPM x MAP
        # correction, even though the trims/diagnosis may be perfectly readable
        # (common on Ford/OBD-II logs that log Absolute Load % instead of MAP).
        if empty_reason is None and platform != "holley" and "map" not in col:
            empty_reason = ("RESULT: no manifold-pressure (MAP) channel, so there's "
                            "no load axis to build the RPM x MAP correction grid.")
    if empty_reason:
        rx = _blocked_prescription(empty_reason, cfg, platform)
    else:
        rx = stages.prescribe(stage, summary, tr.recommendations, platform,
                              airflow_mode=opts.airflow_mode,
                              cam_points=opts.cam_points, spark=spark,
                              architecture=opts.architecture or "gm_gen3_ls")

    try:
        ts = build_timeseries(df, col, stoich=cfg.stoich)
    except Exception:                      # pragma: no cover - defensive
        ts = None

    try:
        from . import channels_ref
        coverage = channels_ref.coverage(col, platform, opts.architecture)
    except Exception:                      # pragma: no cover - defensive
        coverage = None
    return CoreResult(platform=platform, triage=tr, stage=stage, summary=summary,
                      make=opts.make, architecture=opts.architecture,
                      result=result, spark=spark, maf=maf, prescription=rx,
                      empty_reason=empty_reason, findings=findings, notes=notes,
                      timeseries=ts, ve_axes=ve_axes, spark_axes=spark_axes,
                      tables=tables, channel_coverage=coverage)


# --------------------------------------------------------------------------
# Custom VE / base-fuel table axes -- so the correction grid lines up with the
# user's actual table in VCM Editor / Holley (which rarely matches our defaults).
# We can't read the closed binary tune, so the user supplies their table's RPM
# and MAP breakpoints once; we snap each sample to the nearest breakpoint.
# --------------------------------------------------------------------------
def parse_axis(value) -> list:
    """Parse an axis (a list of numbers, or pasted text from the tune table) into
    de-duped breakpoints, PRESERVING the order they were given. Order matters:
    VCM Editor lists MAP ascending down the side (15->105) but Holley lists it
    descending (210->20), so we mirror exactly what the user pasted and only sort
    internally for the snap math -- otherwise a Holley paste comes out flipped.
    Tolerates commas / spaces / tabs / newlines."""
    import re
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        tokens = [str(v) for v in value]
    else:
        tokens = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    seen, ordered = set(), []
    for tk in tokens:
        try:
            v = round(float(tk), 4)
        except (TypeError, ValueError):
            continue
        if v not in seen:
            seen.add(v)
            ordered.append(v)
    return ordered


def parse_ve_table(text) -> dict | None:
    """Parse a whole table pasted via 'Copy with Axis' (VCM Editor) into
    {'rpm': [...], 'map': [...], 'values': [[...]] | None} -- one paste: both
    axes AND the cell values (values power the table-aware spark/VE features;
    axes alone still work for binning).

    The format: a header row of RPM values across the top (often led by '%' and
    trailed by 'rpm'), then one data row per MAP breakpoint where the FIRST value
    is the MAP and the rest are that row's cells, then a trailing 'kPa' label.
    Order is preserved (VCM ascending, Holley descending both survive)."""
    if not isinstance(text, str) or not text.strip():
        return None
    import re
    header_rpm = None
    map_bps: list = []
    rows: list = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        toks = [t for t in re.split(r"[\t,;]+|\s+", ln.strip()) if t]
        if not toks:
            continue
        nums = []
        for t in toks:
            try:
                nums.append(float(t))
            except ValueError:
                pass
        first_is_num = True
        try:
            float(toks[0])
        except ValueError:
            first_is_num = False
        is_header = ("rpm" in (t.lower() for t in toks)) or \
                    (header_rpm is None and not first_is_num and len(nums) >= 2)
        if is_header and header_rpm is None:
            header_rpm = nums                  # the RPM axis (across the top)
            continue
        if not nums:                           # a label-only line like 'kPa'
            continue
        map_bps.append(nums[0])                # leading value of each data row = MAP
        rows.append(nums[1:])                  # the rest of the row = cell values
    if not header_rpm or len(header_rpm) < 2 or len(map_bps) < 2:
        return None
    # values only count when EVERY row carries a full set of cells
    values = rows if rows and all(len(r) == len(header_rpm) for r in rows) else None
    return {"rpm": header_rpm, "map": map_bps, "values": values}


def parse_maf_table(text) -> dict | None:
    """Parse a pasted 1-D MAF calibration (Airflow vs Frequency) into
    {'hz': [...], 'values': [...]}. Accepts either layout: two COLUMNS
    (one 'Hz  value' pair per line, the VCM copy-with-axis shape) or two ROWS
    (Hz header row then a value row)."""
    if not isinstance(text, str) or not text.strip():
        return None
    import re
    numeric_rows = []
    for ln in text.splitlines():
        toks = [t for t in re.split(r"[\t,;]+|\s+", ln.strip()) if t]
        nums = []
        for t in toks:
            try:
                nums.append(float(t))
            except ValueError:
                pass
        if nums:
            numeric_rows.append(nums)
    if len(numeric_rows) >= 4 and all(len(r) == 2 for r in numeric_rows):
        hz = [r[0] for r in numeric_rows]      # column pairs: Hz, value
        vals = [r[1] for r in numeric_rows]
    elif len(numeric_rows) == 2 and len(numeric_rows[0]) == len(numeric_rows[1]) \
            and len(numeric_rows[0]) >= 4:
        hz, vals = numeric_rows                # row pair: Hz header + values
    else:
        return None
    if len(hz) < 4 or hz != sorted(hz):        # a MAF axis is ascending frequency
        return None
    return {"hz": hz, "values": vals}


# The tune tables we capture per platform/generation. `key` is the garage slot;
# labels follow what the user sees in their software (Gen 3 Main VE vs Gen 4+
# VVE; Holley Base Fuel / Timing). MAF is HP Tuners-only (Holley has no MAF).
def table_slots(platform: str, architecture: str | None) -> list:
    arch = architecture or ""
    if platform == "holley":
        return [{"key": "ve", "label": "Base Fuel Table", "kind": "grid"},
                {"key": "spark", "label": "Timing Table", "kind": "grid"}]
    gen3 = "gen3" in arch
    return [{"key": "ve",
             "label": "Main VE table" if gen3 else "VVE (Virtual VE) table",
             "kind": "grid"},
            {"key": "spark", "label": "High Octane Spark table", "kind": "grid"},
            {"key": "maf", "label": "MAF Airflow vs Frequency", "kind": "row"}]


def clean_tables(tables) -> dict | None:
    """Normalize a {'ve': .., 'spark': .., 'maf': ..} spec (each entry either a
    raw paste under {'table': text} or an already-parsed dict) into clean,
    validated tables. Entries that don't parse are dropped; None if nothing
    usable. Grid values are kept only when their shape matches the axes."""
    if not isinstance(tables, dict) or not tables:
        return None
    import datetime
    out = {}
    for key in ("ve", "spark"):
        t = tables.get(key)
        if not isinstance(t, dict):
            continue
        parsed = parse_ve_table(t.get("table")) if t.get("table") else t
        if not isinstance(parsed, dict):
            continue
        axes = clean_ve_axes({"rpm": parsed.get("rpm"), "map": parsed.get("map")})
        if not axes:
            continue
        vals = parsed.get("values")
        ok_shape = (isinstance(vals, list) and len(vals) == len(axes["map"])
                    and all(isinstance(r, list) and len(r) == len(axes["rpm"])
                            for r in vals))
        out[key] = {"rpm": axes["rpm"], "map": axes["map"],
                    "values": vals if ok_shape else None,
                    "pasted": t.get("pasted")
                              or datetime.datetime.now().isoformat(timespec="seconds")}
    m = tables.get("maf")
    if isinstance(m, dict):
        parsed = parse_maf_table(m.get("table")) if m.get("table") else m
        if isinstance(parsed, dict) and parsed.get("hz") and parsed.get("values") \
                and len(parsed["hz"]) == len(parsed["values"]):
            import datetime as _dt
            out["maf"] = {"hz": list(parsed["hz"]), "values": list(parsed["values"]),
                          "pasted": m.get("pasted")
                                    or _dt.datetime.now().isoformat(timespec="seconds")}
    return out or None


def clean_ve_axes(axes) -> dict | None:
    """Validate an axes spec into clean breakpoint lists, or None if unusable.
    Accepts {'rpm':..., 'map':...} (lists or pasted text) OR {'table': '<whole
    Copy-with-Axis paste>'} -- the table is parsed into the two axes first."""
    if not isinstance(axes, dict) or not axes:
        return None
    if axes.get("table"):
        parsed = parse_ve_table(axes.get("table"))
        if parsed:
            axes = parsed
    # sanity bounds: drop obviously-bogus values so a stray paste can't poison it
    rpm = [v for v in parse_axis(axes.get("rpm")) if 0 <= v <= 20000]
    mp = [v for v in parse_axis(axes.get("map")) if 0 <= v <= 400]
    if len(rpm) < 2 or len(mp) < 2:
        return None
    return {"rpm": rpm, "map": mp}


def _axis_edges(bps: list) -> list:
    """Breakpoints -> pd.cut edges that snap each sample to its NEAREST breakpoint
    (midpoints between values; open-ended so nothing falls outside). Sorts first,
    since pd.cut needs monotonic edges -- the user's display order is restored
    afterward in _relabel_to_breakpoints."""
    import numpy as _np
    s = sorted(set(bps))
    mids = [(s[i] + s[i + 1]) / 2.0 for i in range(len(s) - 1)]
    return [-_np.inf] + mids + [_np.inf]


def _relabel_to_breakpoints(result, rpm: list, mp: list, names=(
        "correction", "samples", "confidence", "recommendation", "wb_dev")) -> None:
    """Relabel a result's grids from ascending interval bins to the real
    breakpoint values, then REORDER rows/cols to the user's pasted order (RPM
    rows, MAP columns) so labels + TSV read exactly like the cells in their
    table -- including Holley's descending MAP axis."""
    rpm_sorted, map_sorted = sorted(set(rpm)), sorted(set(mp))
    for name in names:
        g = getattr(result, name, None)
        if g is None or getattr(g, "empty", True):
            continue
        if len(g.index) == len(rpm_sorted) and len(g.columns) == len(map_sorted):
            g = g.copy()
            g.index = pd.Index(rpm_sorted, name="rpm")     # ascending bins -> values
            g.columns = pd.Index(map_sorted, name="map")
            g = g.reindex(index=rpm, columns=mp)           # -> user's display order
            setattr(result, name, g)


def build_timeseries(df, col, max_points: int = 1500, stoich: float = 14.7) -> dict | None:
    """Compact, downsampled traces for the GUI timeline -- the 'point in time'
    view of the log. Returns {t, traces, events, bands} or None if there's no
    usable time/RPM. `bands` are dangerously-lean / overly-rich time ranges so
    the timeline can shade them. Values rounded to keep JSON small."""
    if "rpm" not in col:
        return None
    n = len(df)
    if n < 20:
        return None
    step = max(1, n // max_points)
    idx = range(0, n, step)

    def trace(key, nd=0):
        if key not in col:
            return None
        v = pd.to_numeric(df[col[key]], errors="coerce")
        return [None if pd.isna(x) else round(float(x), nd) for x in v.iloc[idx]]

    t = (pd.to_numeric(df[col["time"]], errors="coerce")
         if "time" in col else pd.Series(range(n)) * 0.05)
    out_t = [round(float(x), 2) if not pd.isna(x) else None for x in t.iloc[idx]]

    traces = {}
    for key, nd in (("rpm", 0), ("map", 1), ("tps", 1), ("afr_actual", 2),
                    ("afr_cmd", 2), ("stft", 1), ("ltft", 1), ("knock", 1),
                    ("ect", 0), ("iat", 0), ("speed", 1), ("maf_freq", 0)):
        tr = trace(key, nd)
        if tr is not None and any(x is not None for x in tr):
            traces[key] = tr
    if "rpm" not in traces:
        return None

    # knock events: timestamps where retard exceeded 1 deg (for timeline markers)
    events = []
    if "knock" in col:
        kn = pd.to_numeric(df[col["knock"]], errors="coerce")
        hits = t[kn > 1.0]
        if len(hits):
            # collapse bursts: keep events at least 1s apart, cap at 50
            last = None
            for ts in hits:
                ts = float(ts)
                if last is None or ts - last >= 1.0:
                    events.append({"t": round(ts, 2), "type": "knock"})
                    last = ts
                if len(events) >= 50:
                    break

    bands = _danger_bands(out_t, traces, stoich)
    return {"t": out_t, "traces": traces, "events": events, "bands": bands}


def _danger_bands(out_t, traces, stoich: float) -> list:
    """Find time ranges where AFR was dangerously lean (under load) or way too
    rich, as {from, to, type} for the timeline to shade. Works in ratio-to-target
    terms so it scales with the fuel (pump vs E85): the target is the commanded
    AFR when logged, else stoich. Lean is gated on being under load -- a lean
    spike on a closed-throttle decel fuel-cut is harmless and must not flag."""
    afr = traces.get("afr_actual")
    if not afr or stoich <= 0:
        return []
    cmd = traces.get("afr_cmd")
    tps = traces.get("tps")
    mp = traces.get("map")
    ect = traces.get("ect")
    LEAN, RICH = 1.06, 0.86       # >=6% leaner than target under load; <=14% richer

    classes = []
    for i, t in enumerate(out_t):
        a = afr[i] if i < len(afr) else None
        if t is None or a is None or a <= 0:
            classes.append(None); continue
        target = cmd[i] if (cmd and i < len(cmd) and cmd[i]) else stoich
        if not target or target <= 0:
            target = stoich
        ratio = a / target
        under_load = ((tps and i < len(tps) and tps[i] is not None and tps[i] >= 50)
                      or (mp and i < len(mp) and mp[i] is not None and mp[i] >= 80))
        warm = not (ect and i < len(ect) and ect[i] is not None and ect[i] < 50)
        if under_load and ratio >= LEAN:
            classes.append("lean")
        elif warm and ratio <= RICH and (not tps or i >= len(tps)
                                         or tps[i] is None or tps[i] >= 8):
            classes.append("rich")
        else:
            classes.append(None)

    # the log's time scale: a band must be wide enough to actually SEE once the
    # whole log is drawn across the chart, but not so wide it overstates a brief
    # blip. Scale to the sample rate and the total span.
    times = [x for x in out_t if x is not None]
    if len(times) < 2:
        return []
    span = times[-1] - times[0]
    dts = [b - a for a, b in zip(times, times[1:]) if b - a > 0]
    med_dt = sorted(dts)[len(dts) // 2] if dts else 0.05
    bridge = max(0.6, med_dt * 2.5)        # merge same-class points across noise gaps
    min_w = max(med_dt * 1.5, span * 0.005)

    bands, cur = [], None
    for i, c in enumerate(classes):
        t = out_t[i]
        if c is None or t is None:
            continue
        if cur and cur["type"] == c and t - cur["to"] <= bridge:
            cur["to"] = t
        else:
            cur = {"from": t, "to": t, "type": c}
            bands.append(cur)
    lo, hi = times[0], times[-1]
    for b in bands:                        # widen thin bands symmetrically, in bounds
        if b["to"] - b["from"] < min_w:
            mid = (b["from"] + b["to"]) / 2.0
            b["from"] = round(max(lo, mid - min_w / 2.0), 2)
            b["to"] = round(min(hi, mid + min_w / 2.0), 2)
    if len(bands) > 40:                    # keep the longest, then restore time order
        bands.sort(key=lambda b: b["to"] - b["from"], reverse=True)
        bands = sorted(bands[:40], key=lambda b: b["from"])
    return bands


# --------------------------------------------------------------------------
# TSV export: paste a correction straight into VCM Editor / Holley.
# VCM Editor: select the matching table region -> Edit -> Paste Special ->
# Multiply by Percentage (VE/MAF) or Add (spark). Tab-separated, no headers; a
# low-confidence cell becomes 0 so a multiply-by-percent leaves it unchanged.
# --------------------------------------------------------------------------
def _tsv_num(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "0"
    s = f"{float(v):.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def grid_tsv(grid, mode: str = "percent") -> str | None:
    """A 2-D correction grid (RPM rows x MAP columns) as paste-ready TSV. mode
    'percent' converts a multiplier (1.05) to +5; 'raw' passes the value through
    (spark degrees)."""
    if grid is None or getattr(grid, "empty", True):
        return None
    rows = []
    for r in grid.index:
        cells = []
        for c in grid.columns:
            v = grid.loc[r, c]
            if pd.isna(v):
                cells.append("0")
            else:
                cells.append(_tsv_num((float(v) - 1.0) * 100.0 if mode == "percent"
                                      else float(v)))
        rows.append("\t".join(cells))
    return "\n".join(rows)


def series_tsv(series, mode: str = "percent") -> str | None:
    """A 1-D correction (e.g. the MAF Airflow-vs-Frequency row) as a single
    tab-separated row."""
    if series is None or getattr(series, "dropna", lambda: series)().empty:
        return None
    cells = []
    for v in series:
        if pd.isna(v):
            cells.append("0")
        else:
            cells.append(_tsv_num((float(v) - 1.0) * 100.0 if mode == "percent" else float(v)))
    return "\t".join(cells)


def correction_tsv(cr) -> str | None:
    """The VE/fuel correction as percent TSV (the main paste target).

    With custom table axes we also transpose to the tuning software's layout --
    VCM Editor's Main VE is RPM across the top (columns) and MAP down the side
    (rows), the opposite of our internal RPM-rows x MAP-cols -- so a Paste Special
    -> Multiply by Percentage drops into the selected table cell-for-cell."""
    grid = getattr(getattr(cr, "result", None), "correction", None)
    if grid is not None and getattr(cr, "ve_axes", None) and not getattr(grid, "empty", True):
        grid = grid.T
    return grid_tsv(grid, "percent")


def ve_abs_tsv(cr) -> str | None:
    """The COMPLETE new VE / base-fuel table -- the user's own values with the
    recommended correction applied -- in the table's own layout (MAP rows x RPM
    cols, the user's paste order). Paste it over the whole table as a plain
    paste (NOT Paste Special -> Multiply). Cells the log didn't cover (or below
    min-samples) keep the ORIGINAL value, never 0. Needs the pasted VE table
    WITH values; the correction grid is already relabeled to those breakpoints
    when the axes came from this table."""
    res = getattr(cr, "result", None)
    grid = getattr(res, "correction", None) if res is not None else None
    t = (getattr(cr, "tables", None) or {}).get("ve")
    if grid is None or getattr(grid, "empty", True) or not getattr(cr, "ve_axes", None) \
            or not t or not t.get("values"):
        return None
    rpm_user, map_user, vals = t["rpm"], t["map"], t["values"]
    if list(grid.index) != list(rpm_user) or list(grid.columns) != list(map_user):
        return None                        # grid wasn't binned to this table
    rows = []
    for mi, mp in enumerate(map_user):
        row = []
        for ri, rp in enumerate(rpm_user):
            try:
                v = float(vals[mi][ri])
            except (IndexError, TypeError, ValueError):
                return None
            m = grid.loc[rp, mp]
            if not pd.isna(m):
                v = v * float(m)
            row.append(_tsv_num(v))
        rows.append("\t".join(row))
    return "\n".join(rows)


def maf_tsv(cr) -> str | None:
    """The MAF Airflow-vs-Frequency correction as a single percent TSV row."""
    maf = getattr(cr, "maf", None)
    return series_tsv(maf[0] if maf else None, "percent")


def _is_add(action) -> bool:
    return str(action) in ("ADD", "AT_CEILING")


def spark_tsv(cr, adds: bool = False) -> str | None:
    """The spark change grid (degrees to add/pull) as TSV -- paste with Add.
    Power ADDs are ALWAYS computed; `adds=False` (the safe default) zeroes them so
    a paste only pulls timing where knock showed. With custom spark axes, transpose
    to the spark table's layout (RPM cols x MAP rows) like the VE grid."""
    sp = getattr(cr, "spark", None)
    grid = getattr(sp, "change", None) if sp else None
    if grid is None:
        return None
    grid = grid.copy()
    if not adds and getattr(sp, "action", None) is not None:
        grid = grid.mask(sp.action.map(_is_add).fillna(False), 0.0)
    if getattr(cr, "spark_axes", None) and not getattr(grid, "empty", True):
        grid = grid.T
    return grid_tsv(grid, "raw")


def spark_abs_tsv(cr, adds: bool = False) -> str | None:
    """The COMPLETE new spark table -- the user's own values with the targets
    applied -- in the table's layout (MAP rows x RPM cols). Paste it over the
    whole table as a plain paste (NOT Paste Special/Add). Cells the log didn't
    cover keep the ORIGINAL value, never 0. `adds=False` also keeps the original
    on power-ADD cells, so the safe default only applies knock pulls."""
    sp = getattr(cr, "spark", None)
    t = (getattr(cr, "tables", None) or {}).get("spark")
    if sp is None or not getattr(sp, "can_run", False) or sp.target is None \
            or not t or not t.get("values"):
        return None
    rpm_user, map_user, vals = t["rpm"], t["map"], t["values"]
    rows = []
    for mi, mp in enumerate(map_user):
        row = []
        for ri, rp in enumerate(rpm_user):
            v = float(vals[mi][ri])
            try:
                if adds or not _is_add(sp.action.loc[rp, mp]):
                    tv = sp.target.loc[rp, mp]  # relabeled to user-order breakpoints
                    if tv == tv:                # not NaN
                        v = float(tv)
            except (KeyError, TypeError):
                pass
            row.append(_tsv_num(v))
        rows.append("\t".join(row))
    return "\n".join(rows)


# --------------------------------------------------------------------------
# JSON serialization (the contract)
# --------------------------------------------------------------------------
def _interval_label(iv) -> str:
    try:
        return f"{int(iv.left)}-{int(iv.right)}"     # default range bins: "400-800"
    except (AttributeError, ValueError, TypeError, OverflowError):
        pass
    if isinstance(iv, (int, float)):                 # custom-axis breakpoint label
        return str(int(iv)) if float(iv).is_integer() else str(iv)
    return str(iv)


def _grid_cells(grid, samples=None, transform=None):
    """DataFrame -> list of {rpm, map, value[, samples]} for non-null cells."""
    if grid is None or grid.empty:
        return []
    out = []
    for r in grid.index:
        for c in grid.columns:
            v = grid.loc[r, c]
            if pd.isna(v):
                continue
            cell = {"rpm": _interval_label(r), "map": _interval_label(c),
                    "value": transform(v) if transform else float(v)}
            if samples is not None and not samples.empty:
                try:
                    cell["samples"] = int(samples.loc[r, c])
                except (KeyError, ValueError):
                    pass
            out.append(cell)
    return out


def result_to_dict(cr: CoreResult) -> dict:
    tr = cr.triage
    s = cr.summary
    d = {
        "platform": cr.platform,
        "platform_label": platform_label(cr.platform),
        "make": cr.make,
        "architecture": cr.architecture,
        "triage": {"state": tr.state, "can_correct": tr.can_correct,
                   "detail": tr.detail, "recommendations": list(tr.recommendations)},
        "stage": cr.stage,
        "summary": {
            "coverage_pct": round(s.coverage_pct, 1), "n_confident": s.n_confident,
            "median_pct": s.median_pct, "max_abs_pct": s.max_abs_pct,
            "cruise_max_abs_pct": s.cruise_max_abs_pct, "wot_covered": s.wot_covered,
            "wot_max_abs_pct": s.wot_max_abs_pct, "offset": s.offset,
            "o2_suspect_cells": s.o2_suspect_cells, "has_wideband": s.has_wideband,
            "converged": s.converged,
        },
        "notes": list(cr.notes),
        "empty_reason": cr.empty_reason,
        "findings": [f.to_dict() for f in cr.findings],
    }

    res = cr.result
    if res is not None and getattr(res, "correction", None) is not None:
        d["correction"] = {
            "unit": "percent_change",
            "cells": _grid_cells(res.correction, getattr(res, "samples", None),
                                 transform=lambda v: round((v - 1.0) * 100, 2)),
        }
        # table-aware: when the user's VE/base-fuel table (with values) is on
        # file and the grid was binned to its axes, every cell also carries the
        # absolute current -> target value (current x the correction).
        ve_t = (cr.tables or {}).get("ve") if cr.tables else None
        grid = res.correction
        if ve_t and ve_t.get("values") and cr.ve_axes \
                and list(grid.index) == list(ve_t["rpm"]) \
                and list(grid.columns) == list(ve_t["map"]):
            cur_at = {}
            for mi, mp in enumerate(ve_t["map"]):
                for ri, rp in enumerate(ve_t["rpm"]):
                    try:
                        cur_at[(_interval_label(rp), _interval_label(mp))] = \
                            float(ve_t["values"][mi][ri])
                    except (IndexError, TypeError, ValueError):
                        pass
            for c in d["correction"]["cells"]:
                cur = cur_at.get((c["rpm"], c["map"]))
                if cur is not None:
                    c["current"] = round(cur, 2)
                    c["target"] = round(cur * (1.0 + c["value"] / 100.0), 2)
            d["correction"]["has_table"] = True
        rec = getattr(res, "recommendation", None)
        if rec is not None and not rec.empty:
            d["cross_check"] = [{"rpm": c["rpm"], "map": c["map"], "label": c["value"]}
                                for c in _grid_cells(rec, transform=lambda v: v)]
        d["safety"] = [{"type": e.get("type"), "time": e.get("time"),
                        "detail": e.get("detail")} for e in getattr(res, "safety", [])]

    if cr.maf[0] is not None:
        series, counts = cr.maf[0], cr.maf[1]
        cells = []
        for fb in series.index:
            v = series.loc[fb]
            if pd.isna(v):
                continue
            cell = {"hz": _interval_label(fb), "pct": round((v - 1.0) * 100, 2)}
            if counts is not None and fb in counts.index:
                cell["samples"] = int(counts.loc[fb])
            cells.append(cell)
        d["maf"] = {"axis": "frequency_hz", "cells": cells}

    sp = cr.spark
    if sp is not None:
        if not sp.can_run:
            d["spark"] = {"can_run": False, "reason": sp.reason}
        else:
            cells = []
            for c in _grid_cells(sp.change, transform=lambda v: round(float(v), 1)):
                r, m = c["rpm"], c["map"]
                ri = _find_interval(sp.action.index, r)
                ci = _find_interval(sp.action.columns, m)
                act = sp.action.loc[ri, ci]
                cell = {"rpm": r, "map": m, "deg": c["value"],
                        "action": None if pd.isna(act) else str(act)}
                # table-aware: absolute current -> target per cell
                if sp.current is not None:
                    cur = sp.current.loc[ri, ci]
                    tgt = sp.target.loc[ri, ci] if sp.target is not None else None
                    if not pd.isna(cur):
                        cell["current"] = round(float(cur), 1)
                    if tgt is not None and not pd.isna(tgt):
                        cell["target"] = round(float(tgt), 1)
                cells.append(cell)
            d["spark"] = {"can_run": True, "knock_cells": sp.knock_cells,
                          "advisory": sp.advisory, "pullback": list(sp.pullback),
                          "notes": list(sp.notes),
                          "table_findings": list(sp.table_findings),
                          "has_table": sp.current is not None,
                          "find_power": bool(sp.find_power),
                          "unit": "degrees_change", "cells": cells}

    rx = cr.prescription
    if rx is not None:
        d["prescription"] = {"stage": rx.stage, "title": rx.title,
                             "rationale": rx.rationale, "actions": list(rx.actions),
                             "drive": rx.drive, "capture": list(rx.capture),
                             "converged": rx.converged}

    # --- additive GUI keys (v2): journey ladder, traces, paste-ready TSV ---
    d["journey"] = [{"key": k, "title": t} for k, t in stages.STAGES]
    if cr.timeseries:
        d["timeseries"] = cr.timeseries
    if cr.ve_axes:
        d["ve_axes"] = cr.ve_axes          # the custom VE table axes the grid used
    if cr.spark_axes:
        d["spark_axes"] = cr.spark_axes    # the custom spark table axes used
    if cr.channel_coverage:
        d["channel_coverage"] = cr.channel_coverage
    tsv = {}
    builders = [("correction", lambda: correction_tsv(cr)),
                ("ve_abs", lambda: ve_abs_tsv(cr)),
                ("maf", lambda: maf_tsv(cr)),
                # spark: pulls-only (safe default) + a *_power variant with the
                # ADDs applied; the GUI picks by the "Add power" toggle.
                ("spark", lambda: spark_tsv(cr, adds=False)),
                ("spark_power", lambda: spark_tsv(cr, adds=True)),
                ("spark_abs", lambda: spark_abs_tsv(cr, adds=False)),
                ("spark_abs_power", lambda: spark_abs_tsv(cr, adds=True))]
    for name, fn in builders:
        try:
            v = fn()
        except Exception:                  # pragma: no cover - defensive
            v = None
        if v:
            tsv[name] = v
    if tsv:
        d["tsv"] = tsv
    return d


# --------------------------------------------------------------------------
# Before/after comparison -- operate on two to_dict() payloads (the stable
# contract) so it's UI-agnostic and testable.
# --------------------------------------------------------------------------
def _corr_cells(d: dict) -> dict:
    return {(c["rpm"], c["map"]): c["value"]
            for c in (d.get("correction") or {}).get("cells", [])}


def _knock_count(d: dict) -> int:
    ts = d.get("timeseries") or {}
    return max(len(ts.get("events") or []), (d.get("spark") or {}).get("knock_cells", 0) or 0)


def _band_count(d: dict, typ: str) -> int:
    ts = d.get("timeseries") or {}
    return sum(1 for x in (ts.get("bands") or []) if x.get("type") == typ)


def compare_results(a: dict, b: dict) -> dict:
    """Diff two analyses (each a to_dict() payload), a=before, b=after. Returns
    metric deltas, findings resolved/new/persisting (by id), stage movement, an
    optional per-cell correction delta (when the grids share axes), and a plain-
    English headline. 'better' is direction-aware (less fuel error / less knock)."""
    sa, sb = a.get("summary") or {}, b.get("summary") or {}

    def metric(label, key, lower_better=True, src_a=sa, src_b=sb):
        va, vb = src_a.get(key), src_b.get(key)
        out = {"label": label, "a": va, "b": vb}
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            out["a"] = float(va); out["b"] = float(vb)
            out["delta"] = round(float(vb) - float(va), 2)
            out["better"] = bool((vb < va) if lower_better else (vb > va))   # native bool for JSON
            out["same"] = bool(vb == va)
        return out

    ka, kb = _knock_count(a), _knock_count(b)
    lean_a, lean_b = _band_count(a, "lean"), _band_count(b, "lean")
    rich_a, rich_b = _band_count(a, "rich"), _band_count(b, "rich")
    metrics = [
        metric("Worst cell (|%|)", "max_abs_pct"),
        metric("Cruise worst (|%|)", "cruise_max_abs_pct"),
        metric("WOT worst (|%|)", "wot_max_abs_pct"),
        metric("Cells covered", "n_confident", lower_better=False),
        {"label": "Knock cells/events", "a": ka, "b": kb, "delta": kb - ka,
         "better": kb < ka, "same": kb == ka},
        {"label": "Lean-under-load spots", "a": lean_a, "b": lean_b,
         "delta": lean_b - lean_a, "better": lean_b < lean_a, "same": lean_b == lean_a},
        {"label": "Too-rich spots", "a": rich_a, "b": rich_b,
         "delta": rich_b - rich_a, "better": rich_b < rich_a, "same": rich_b == rich_a},
    ]

    fa = {f["id"]: f for f in a.get("findings", [])}
    fb = {f["id"]: f for f in b.get("findings", [])}
    findings = {
        "resolved": [fa[i] for i in fa if i not in fb],
        "new": [fb[i] for i in fb if i not in fa],
        "persisting": [fb[i] for i in fb if i in fa],
    }

    journey = b.get("journey") or a.get("journey") or []
    order = {s["key"]: i for i, s in enumerate(journey)}
    sga, sgb = a.get("stage"), b.get("stage")
    stage = {"a": sga, "b": sgb,
             "advanced": bool(order.get(sgb, -1) > order.get(sga, -1)) if order else None}

    ca, cb = _corr_cells(a), _corr_cells(b)
    shared = sorted(set(ca) & set(cb), key=lambda k: (str(k[0]), str(k[1])))
    corr_delta = [{"rpm": k[0], "map": k[1], "a": ca[k], "b": cb[k],
                   "delta": round(abs(cb[k]) - abs(ca[k]), 2)} for k in shared]

    # headline: lead with the worst-cell fuel error trend
    headline = "Compared two logs."
    va, vb = sa.get("max_abs_pct"), sb.get("max_abs_pct")
    if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va:
        pct = round((abs(va) - abs(vb)) / abs(va) * 100)
        if abs(va) - abs(vb) > 0.2:
            headline = f"Worst-cell fuel error dropped {pct}% (±{abs(va):g}% → ±{abs(vb):g}%)."
        elif abs(vb) - abs(va) > 0.2:
            headline = f"Worst-cell fuel error grew (±{abs(va):g}% → ±{abs(vb):g}%) — check what changed."
        else:
            headline = f"Fuel error about the same (±{abs(va):g}% → ±{abs(vb):g}%)."
    if findings["resolved"]:
        headline += f" {len(findings['resolved'])} issue(s) cleared."
    if findings["new"]:
        headline += f" {len(findings['new'])} new."

    return {"headline": headline, "metrics": metrics, "findings": findings,
            "stage": stage, "correction_delta": corr_delta}


def diff_table(old: dict, new: dict) -> dict | None:
    """Diff two versions of the SAME pasted tune table (a garage table_history
    entry vs the current one). Cells match by breakpoint VALUE, not position, so
    an axis edit between pastes only drops the moved cells from the comparison.
    Handles 2-D grids (rpm/map/values) and the 1-D MAF calibration (hz/values).
    Returns {changed, compared, max_delta, at, cells} or None when either side
    lacks values (or nothing is comparable). cells are sorted biggest-|delta|
    first and capped so a whole-table repaste stays JSON-friendly."""
    if not isinstance(old, dict) or not isinstance(new, dict) \
            or not old.get("values") or not new.get("values"):
        return None
    cells = []
    if "hz" in old or "hz" in new:                       # 1-D MAF calibration
        o = dict(zip(old.get("hz") or [], old["values"]))
        n = dict(zip(new.get("hz") or [], new["values"]))
        shared = [h for h in (new.get("hz") or []) if h in o]
        for h in shared:
            try:
                b, a = float(o[h]), float(n[h])
            except (TypeError, ValueError):
                continue
            if round(a - b, 2):
                cells.append({"hz": h, "before": round(b, 2), "after": round(a, 2),
                              "delta": round(a - b, 2)})
    else:                                                # 2-D grid
        def index(t):
            out = {}
            for mi, mp in enumerate(t.get("map") or []):
                for ri, rp in enumerate(t.get("rpm") or []):
                    try:
                        out[(rp, mp)] = float(t["values"][mi][ri])
                    except (IndexError, TypeError, ValueError):
                        pass
            return out
        o, n = index(old), index(new)
        shared = [k for k in n if k in o]
        for rp, mp in shared:
            b, a = o[(rp, mp)], n[(rp, mp)]
            if round(a - b, 2):
                cells.append({"rpm": rp, "map": mp, "before": round(b, 2),
                              "after": round(a, 2), "delta": round(a - b, 2)})
    if not shared:
        return None
    cells.sort(key=lambda c: -abs(c["delta"]))
    worst = cells[0] if cells else None
    return {"changed": len(cells), "compared": len(shared),
            "max_delta": worst["delta"] if worst else 0.0,
            "at": ({k: worst[k] for k in ("rpm", "map", "hz") if k in worst}
                   if worst else None),
            "cells": cells[:200]}


def _find_interval(index, label: str):
    """Map a '1200-1600' label back to the matching Interval in an index."""
    for iv in index:
        if _interval_label(iv) == label:
            return iv
    return index[0]
