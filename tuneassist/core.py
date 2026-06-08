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
                       architecture=record.get("architecture") or d_arch)
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
# docs/TUNING_BY_PLATFORM.md for how each is tuned.
ARCHITECTURES = {
    "gm_gen3_ls": "GM Gen 3 LS (VE table + MAF)",
    "gm_gen4_ls": "GM Gen 4 LS (Virtual VE / MAF-only)",
    "gm_gen5_lt": "GM Gen 5 LT (direct injection)",
    "ford_coyote": "Ford Coyote (MAF / load %)",
    "holley_selflearn": "Holley self-learning",
    "generic": "Generic / other",
}


def default_make_arch(platform: str) -> tuple[str, str]:
    """Sensible (make, architecture) when none was chosen/detected. Gen 3 LS is
    the most common swap, so it's the GM default."""
    if platform == "holley":
        return "gm", "holley_selflearn"
    return "gm", "gm_gen3_ls"


def detect_make(df) -> str | None:
    """Best-effort engine make from the channel set. Ford/OBD-II logs use the
    'WB EQ Ratio' wideband; 'Absolute Load' alone is NOT Ford (GM Gen 4 logs it
    too) -- only treat it as Ford when there's no manifold MAP. Default GM."""
    cols = " ".join(str(c).lower() for c in df.columns)
    has_map = "manifold absolute pressure" in cols
    if "wb eq ratio" in cols or "coyote" in cols:
        return "ford"
    if "absolute load" in cols and not has_map:
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
        "IDLE_TIMING_SWING": "idle_spark", "WARMUP_RICH": "warmup_enr",
        "WARMUP_LEAN": "warmup_enr", "ENRICH_NOT_DECAYED": "ase",
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
    if tr.can_correct:
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        try:
            if platform == "holley":
                corr, counts, _disagree, hnotes = holley.analyze_holley(df, cfg)
                result = _HolleyResult(corr, counts, hnotes)
                if out_dir and not corr.empty:
                    corr.to_csv(os.path.join(out_dir, "holley_base_fuel_correction.csv"))
            else:
                result = analyze(df, cfg)
                if out_dir:
                    write_report(result, out_dir)
                if opts.airflow_mode == "maf":
                    maf = maf_correction(df, cfg)
            summary = stages.summarize(result, cfg)
        except ValueError as e:
            notes.append(f"Could not analyze: {e}")
        if opts.tune_spark:
            try:
                spark = analyze_spark(df, cfg, find_power=opts.find_power,
                                      profile=opts.profile)
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

    return CoreResult(platform=platform, triage=tr, stage=stage, summary=summary,
                      make=opts.make, architecture=opts.architecture,
                      result=result, spark=spark, maf=maf, prescription=rx,
                      empty_reason=empty_reason, findings=findings, notes=notes)


# --------------------------------------------------------------------------
# JSON serialization (the contract)
# --------------------------------------------------------------------------
def _interval_label(iv) -> str:
    try:
        return f"{int(iv.left)}-{int(iv.right)}"
    except (AttributeError, ValueError, TypeError):
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
                act = sp.action.loc[_find_interval(sp.action.index, r),
                                    _find_interval(sp.action.columns, m)]
                cells.append({"rpm": r, "map": m, "deg": c["value"],
                              "action": None if pd.isna(act) else str(act)})
            d["spark"] = {"can_run": True, "knock_cells": sp.knock_cells,
                          "advisory": sp.advisory, "pullback": list(sp.pullback),
                          "unit": "degrees_change", "cells": cells}

    rx = cr.prescription
    if rx is not None:
        d["prescription"] = {"stage": rx.stage, "title": rx.title,
                             "rationale": rx.rationale, "actions": list(rx.actions),
                             "drive": rx.drive, "capture": list(rx.capture),
                             "converged": rx.converged}
    return d


def _find_interval(index, label: str):
    """Map a '1200-1600' label back to the matching Interval in an index."""
    for iv in index:
        if _interval_label(iv) == label:
            return iv
    return index[0]
