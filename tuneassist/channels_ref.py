"""
channels_ref.py -- the canonical "what to log" reference, per platform/generation,
plus a coverage check against a real log.

Two jobs, one source of truth:
  * a copyable reference list the GUI shows as a popout (and the onboarding guide
    reuses) -- the exact VCM Scanner / Holley channel names to record;
  * coverage(): given the columns we resolved from a dropped log, report what's
    present and what's missing, with WHY each missing one matters -- so the user
    knows exactly what to add before the next pull. That closes the loop on the
    #1 reason logs come in unanalyzable: the right channels weren't recorded.

`key` is the internal resolve_columns() canonical (None = reference-only, e.g.
torque/advance breakdown PIDs we don't need but a tuner likes to see). `tier`:
  essential   -- analysis is crippled without it
  recommended -- unlocks a phase / sharpens the advice
  reference   -- nice to have; not coverage-checked

Channel display names follow what the user actually sees in their software so the
list is copy-and-find friendly.
"""
from __future__ import annotations

# C(name, key, tier, why) -- `why` shown when an essential/recommended one is missing.
def _C(name, key=None, tier="reference", why=""):
    return {"name": name, "key": key, "tier": tier, "why": why}


# --- shared LS core (Gen 3 & Gen 4) ---
_LS_CORE = [
    _C("Engine RPM", "rpm", "essential", "nothing can be binned without RPM"),
    _C("Throttle Position Sensor", "tps", "essential", "separates cruise from WOT"),
    _C("Mass Airflow (Hz)", "maf_freq", "recommended", "needed to tune the MAF curve"),
    _C("Mass Airflow (g/s)", "maf_air", "recommended", "the airflow reference for MAF work"),
    _C("Manifold Absolute Pressure (kPa)", "map", "essential", "the load axis for the VE grid"),
    _C("Engine Coolant Temp", "ect", "essential", "used to drop invalid warm-up data"),
    _C("Intake Air Temp", "iat", "recommended", "heat-soak and IAT-spark checks"),
    _C("Ignition Timing Advance", "spark", "recommended", "the spark baseline per cell"),
    _C("Knock Retard", "knock", "essential", "no spark/timing advice without it"),
    _C("Short Term Fuel Trim (Bank 1)", "stft", "essential", "fuel trims drive the VE correction"),
    _C("Short Term Fuel Trim (Bank 2)", "stft2", "recommended", "catches a per-bank imbalance"),
    _C("Long Term Fuel Trim (Bank 1)", "ltft", "essential", "the learned half of the fuel error"),
    _C("Long Term Fuel Trim (Bank 2)", "ltft2", "recommended", "catches a per-bank imbalance"),
]

# --- Gen 3 (P01/P59) specifics ---
GEN3 = _LS_CORE + [
    _C("Commanded AFR", "afr_cmd", "essential", "the target to correct fueling toward"),
    _C("WB AFR (Bank 1)", "afr_actual", "recommended", "WOT fueling is a guess without a wideband"),
    _C("WB EQ Ratio (Bank 1)", "afr_actual", "reference"),
    _C("Injector Pulse Width (Bank 1)", "injpw", "reference"),
    _C("Cylinder Airmass", "airmass", "recommended", "the SD airmass reference"),
    _C("Fuel Trim Cell", None, "reference"),
    _C("DFCO Status", None, "reference"),
]

# --- Gen 4 (E38/E67) specifics: torque-model + extra spark-advance breakdown ---
GEN4 = _LS_CORE + [
    _C("Dynamic Airflow", "sd_air", "recommended", "the SD airmass reference for Gen 4"),
    _C("Intake Valve Temp (IVT)", None, "reference"),
    _C("Commanded Equivalence Ratio", "eq_cmd", "essential", "the fueling target (Gen 4 is EQ-based)"),
    _C("WB EQ Ratio 1", "afr_actual", "recommended", "WOT fueling is a guess without a wideband"),
    _C("Delivered Engine Torque", "torque", "reference"),
    _C("Torque Management Advance", None, "reference"),
    _C("FLEX Advance", None, "reference"),
    _C("IAT Advance", None, "reference"),
    _C("PE Advance", None, "reference"),
    _C("Brake Torque Management", None, "reference"),
    _C("Immediate Axle Torque", None, "reference"),
    _C("Predicted Axle Torque", None, "reference"),
    _C("Fuel Trim Cell", None, "reference"),
]

# --- Holley (Sniper / Terminator X): logs a full set by default. Keys here are
#     Holley's resolver canonicals (cts/mat/ign/afr_target/cl_comp/learn), NOT the
#     GM ones, so coverage() matches a real Holley log. ---
HOLLEY = [
    _C("RPM", "rpm", "essential", "nothing can be binned without RPM"),
    _C("MAP", "map", "essential", "the load axis for the base-fuel grid"),
    _C("TPS", "tps", "essential", "separates cruise from WOT"),
    _C("Air/Fuel Ratio (wideband)", "afr_actual", "essential", "Holley's built-in wideband drives everything"),
    _C("Target AFR", "afr_target", "essential", "the target the wideband is corrected toward"),
    _C("Closed Loop Comp", "cl_comp", "recommended", "the live fuel correction"),
    _C("Learn (self-tune)", "learn", "recommended", "the learned fuel correction"),
    _C("Coolant Temp (CTS)", "cts", "essential", "used to drop invalid warm-up data"),
    _C("Air Temp (IAT/MAT)", "mat", "recommended", "heat-soak checks"),
    _C("Ignition Timing", "ign", "recommended", "the spark baseline per cell"),
    _C("Knock (if equipped)", "knock", "recommended", "no spark/timing advice without it"),
    _C("Vehicle Speed (if wired)", "speed", "reference"),
]


def _group(platform: str, architecture: str | None) -> tuple[str, list]:
    """(label, channel list) for the platform/architecture."""
    arch = architecture or ""
    if platform == "holley":
        return ("Holley EFI", HOLLEY)
    if "gen3" in arch:
        return ("HP Tuners — Gen 3 LS (P01/P59)", GEN3)
    if "gen4" in arch:
        return ("HP Tuners — Gen 4 LS (E38/E67)", GEN4)
    if "gen5" in arch:
        # Gen 5 LT shares the Gen 4 torque/EQ model for logging purposes.
        return ("HP Tuners — Gen 5 LT", GEN4)
    return ("HP Tuners — Gen 4 LS (E38/E67)", GEN4)   # sane default for HP Tuners


def reference() -> dict:
    """The whole reference, keyed for the GUI popout to pick the right list."""
    out = {}
    for plat, arch in (("gm", "gm_gen3_ls"), ("gm", "gm_gen4_ls"),
                       ("gm", "gm_gen5_lt"), ("holley", None)):
        label, chans = _group(plat, arch)
        key = "holley" if plat == "holley" else arch
        out[key] = {"label": label,
                    "channels": [{"name": c["name"], "tier": c["tier"]} for c in chans]}
    return out


def coverage(col: dict, platform: str, architecture: str | None) -> dict:
    """Given resolved columns (canonical -> actual), report logging coverage:
    which essential/recommended channels are present vs missing (with why).
    Reference-only channels and duplicate keys are ignored."""
    _label, chans = _group(platform, architecture)
    present, missing, seen = [], [], set()
    for c in chans:
        key, tier = c["key"], c["tier"]
        if key is None or tier == "reference" or key in seen:
            continue
        seen.add(key)
        if key in col:
            present.append(c["name"])
        else:
            missing.append({"name": c["name"], "tier": tier, "why": c["why"]})
    ess_missing = [m for m in missing if m["tier"] == "essential"]
    return {"present": present, "missing": missing,
            "n_present": len(present), "n_missing": len(missing),
            "ok": not ess_missing}
