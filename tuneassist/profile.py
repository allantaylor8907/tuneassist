"""
profile.py -- optional engine profile -> tailored spark guidance.

Two builds can share a cam class yet have very different knock margins. A high-
compression aluminum LS sheds heat but detonates sooner; a low-compression iron
5.3 tolerates more static timing but holds heat and creeps into knock on back-to-
back pulls. Compression and block material move the *spark ceiling* and change
*when you pull timing back*, so we capture them (all optional) and tailor the
advisory + the pull-back checklist. Boost/nitrous dominate everything when present.

Guidance only, knock-governed downstream -- nothing here is auto-applied.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class EngineProfile:
    block: str | None = None          # 'iron' | 'alum'
    compression: float | None = None  # static CR, e.g. 9.5, 10.5
    displacement: float | None = None # liters, informational
    power_adder: str = "na"           # 'na' | 'boost' | 'nitrous'
    engine: str | None = None         # preset label, e.g. "Chevy LS1 5.7"
    mods: list = field(default_factory=list)   # ["Ported heads", ...]


# Common engine presets -> (label, displacement L, block, static compression).
# Pick-a-engine instead of asking for block/CR by hand. Approximate stock CR.
ENGINE_PRESETS = [
    # --- GM LS Gen 3 (1997-2007, 24x) ---
    ("Chevy LS 4.8 (iron)",            4.8, "iron", 9.5),
    ("Chevy LS 5.3 (iron truck)",      5.3, "iron", 9.5),
    ("Chevy LS 5.3 (aluminum)",        5.3, "alum", 9.6),
    ("Chevy LS 6.0 LQ4 (iron)",        6.0, "iron", 9.4),
    ("Chevy LS 6.0 LQ9 (iron)",        6.0, "iron", 10.0),
    ("Chevy LS1 5.7 (aluminum)",       5.7, "alum", 10.1),
    ("Chevy LS6 5.7 (aluminum)",       5.7, "alum", 10.5),
    # --- GM LS Gen 4 (2005-2017, 58x, VVT/AFM) ---
    ("Chevy LS2 6.0 (aluminum)",       6.0, "alum", 10.9),
    ("Chevy LS3 6.2 (aluminum)",       6.2, "alum", 10.7),
    ("Chevy LS7 7.0 (aluminum)",       7.0, "alum", 11.0),
    ("Chevy L92/L94 6.2 (aluminum)",   6.2, "alum", 10.5),
    ("Chevy LY6 6.0 (iron)",           6.0, "iron", 9.6),
    ("Chevy LS 4.8/5.3 Gen 4 (iron)",  5.3, "iron", 9.7),
    ("Chevy LSA 6.2 supercharged",     6.2, "alum", 9.1),
    ("Chevy LS9 6.2 supercharged",     6.2, "alum", 9.1),
    # --- GM LT Gen 5 (2014+, direct injection) ---
    ("Chevy LT1 6.2 (aluminum)",       6.2, "alum", 11.5),
    ("Chevy LT4 6.2 supercharged",     6.2, "alum", 10.0),
    ("Chevy L83 5.3 (aluminum)",       5.3, "alum", 11.0),
    ("Chevy L86 6.2 (aluminum)",       6.2, "alum", 11.5),
    ("Chevy L8T 6.6 (iron)",           6.6, "iron", 10.8),
    # --- Chevy Gen I small-block (carb'd classics -> Holley) ---
    ("Chevy SBC 305 (iron)",           5.0, "iron", 9.5),
    ("Chevy SBC 327 (iron)",           5.4, "iron", 9.0),
    ("Chevy SBC 350 (iron)",           5.7, "iron", 9.0),
    ("Chevy SBC 383 stroker",          6.3, "iron", 9.5),
    ("Chevy SBC 400 (iron)",           6.6, "iron", 8.5),
    # --- Chevy big-block (carb'd classics -> Holley) ---
    ("Chevy BBC 396 (iron)",           6.5, "iron", 9.0),
    ("Chevy BBC 427 (iron)",           7.0, "iron", 9.0),
    ("Chevy BBC 454 (iron)",           7.4, "iron", 8.5),
    ("Chevy BBC 502 crate (iron)",     8.2, "iron", 9.6),
    # --- Ford modern (HP Tuners) ---
    ("Ford 4.6 modular 2V/3V",         4.6, "alum", 9.8),
    ("Ford 5.4 modular",               5.4, "alum", 9.8),
    ("Ford Coyote 5.0",                5.0, "alum", 11.0),
    ("Ford Godzilla 7.3 (iron)",       7.3, "iron", 10.5),
    # --- Ford classics (carb'd -> Holley) ---
    ("Ford 289 (iron)",                4.7, "iron", 9.0),
    ("Ford 5.0 / 302 (iron)",          5.0, "iron", 9.0),
    ("Ford 347 stroker",               5.7, "iron", 9.5),
    ("Ford 351W (iron)",               5.8, "iron", 9.0),
    ("Ford 351C Cleveland (iron)",     5.8, "iron", 9.0),
    ("Ford 390 FE (iron)",             6.4, "iron", 9.5),
    ("Ford 428 FE (iron)",             7.0, "iron", 9.0),
    ("Ford BBF 460 (iron)",            7.5, "iron", 8.5),
    # --- Mopar Gen 3 HEMI (2003+, HP Tuners) ---
    ("Mopar HEMI 5.7",                 5.7, "iron", 10.5),
    ("Mopar HEMI 6.1",                 6.1, "iron", 10.3),
    ("Mopar HEMI 6.4",                 6.4, "iron", 10.9),
    ("Mopar Hellcat 6.2 supercharged", 6.2, "iron", 9.5),
    # --- Mopar classics (carb'd -> Holley) ---
    ("Mopar 318 LA (iron)",            5.2, "iron", 9.0),
    ("Mopar 340 LA (iron)",            5.6, "iron", 9.5),
    ("Mopar 360 LA (iron)",            5.9, "iron", 8.5),
    ("Mopar 383 B (iron)",             6.3, "iron", 9.5),
    ("Mopar 400 B (iron)",             6.6, "iron", 8.2),
    ("Mopar 426 HEMI (iron)",          7.0, "iron", 9.5),
    ("Mopar 440 RB (iron)",            7.2, "iron", 9.0),
    # --- Pontiac ---
    ("Pontiac 350 (iron)",             5.7, "iron", 8.0),
    ("Pontiac 389 (iron)",             6.4, "iron", 8.6),
    ("Pontiac 400 (iron)",             6.6, "iron", 8.5),
    ("Pontiac 428 (iron)",             7.0, "iron", 8.5),
    ("Pontiac 455 (iron)",             7.5, "iron", 8.4),
    # --- Buick ---
    ("Buick 350 (iron)",               5.7, "iron", 9.0),
    ("Buick 455 (iron)",               7.5, "iron", 8.5),
    ("Buick 3.8 Turbo V6 (Grand National)", 3.8, "iron", 8.0),
    # --- Oldsmobile ---
    ("Olds 350 Rocket (iron)",         5.7, "iron", 9.0),
    ("Olds 455 (iron)",                7.5, "iron", 8.5),
    # --- AMC / Jeep ---
    ("AMC 304 (iron)",                 5.0, "iron", 8.4),
    ("AMC 360 (iron)",                 5.9, "iron", 8.5),
    ("AMC 401 (iron)",                 6.6, "iron", 9.5),
]

# Common bolt-on mods (context; stored on the profile and shown in the summary).
COMMON_MODS = ["Ported heads", "Long-tube headers", "Cold-air intake",
               "Intake manifold swap", "Bigger throttle body", "Larger injectors",
               "Aftermarket cam", "Nitrous", "Turbo", "Supercharger"]


def preset_to_profile(label: str, power_adder: str = "na", mods=None):
    """Build an EngineProfile from a preset label (or None for custom)."""
    for lbl, disp, block, cr in ENGINE_PRESETS:
        if lbl == label:
            return EngineProfile(block=block, compression=cr, displacement=disp,
                                 power_adder=power_adder, engine=lbl, mods=list(mods or []))
    return None


def spark_bounds(profile: EngineProfile | None, stoich: float = 14.7) -> tuple:
    """The numeric WOT-total-timing sanity window (lo, hi) behind
    spark_guidance()'s advisory text -- used to CAP table-aware spark ADDs.
    Mirrors the same rules: boost ~10-18, nitrous ~20-26 (shot-dependent),
    NA 24-28 shifted by compression, +2 on E85. A ceiling, never a target."""
    e85 = stoich < 11
    pa = (profile.power_adder if profile else "na") or "na"
    if pa == "boost":
        return (10, 18)
    if pa == "nitrous":
        return (20, 26)
    lo, hi = 24, 28
    cr = profile.compression if profile else None
    if cr is not None:
        if cr >= 11.0:
            lo, hi = 22, 25
        elif cr <= 9.7:
            lo, hi = 25, 29
    if e85:
        lo, hi = lo + 2, hi + 2
    return (lo, hi)


def spark_guidance(profile: EngineProfile | None, stoich: float = 14.7):
    """Return (advisory_str, pullback_conditions[list]).

    advisory: a sanity ceiling for WOT total timing (never a target).
    pullback_conditions: the universal + build-specific "pull timing when..." list.
    """
    e85 = stoich < 11
    pa = (profile.power_adder if profile else "na") or "na"

    # Universal "pull it back when..." checklist (the user asked specifically).
    pull = [
        "Knock retard shows up (any sustained) -- pull that cell now, ask questions later.",
        "Charge temp (IAT) climbs -- hot day, heat-soak, or back-to-back pulls; hot air "
        "detonates. Make sure the IAT spark-retard table is doing its job.",
        "Coolant runs hot -- a hot engine has less knock margin than a cool one.",
        "AFR leans out under load -- lean + advance is how ring-lands die; fix fuel first.",
        "Lower-octane fill, big altitude drop, or much hotter weather than you tuned in.",
    ]

    if pa == "boost":
        advisory = ("Boosted: WOT timing lives FAR below NA -- often ~10-18 total, and "
                    "less the more boost you run. Sneak up in 1 deg steps watching knock; "
                    "every extra psi shrinks the margin.")
        pull.append("Any time you raise boost -- timing must come down with it.")
        return advisory, pull
    if pa == "nitrous":
        advisory = ("Nitrous: pull roughly ~2 deg per 50 hp of shot off your NA timing as a "
                    "starting point, then knock-verify. Add back only with margin.")
        pull.append("On the bottle vs off it are two different tunes -- don't share timing.")
        return advisory, pull

    # --- naturally aspirated ---
    lo, hi = 24, 28
    cr = profile.compression if profile else None
    extra = []
    if cr is not None:
        if cr >= 11.0:
            lo, hi = 22, 25
            extra.append(f"{cr:g}:1 is high compression -- less timing headroom; small "
                         "changes matter and knock comes on fast. Lean to the low end.")
            pull.append("High static compression means thinner margin everywhere -- "
                        "treat a clean pull as the ceiling, not an invitation.")
        elif cr <= 9.7:
            lo, hi = 25, 29
            extra.append(f"{cr:g}:1 is on the low side -- usually tolerates a bit more "
                         "advance, but it's still knock-governed.")
    if e85:
        lo, hi = lo + 2, hi + 2
    advisory = (f"Advisory ceiling: this NA build on {'E85' if e85 else 'pump'} typically "
                f"lands ~{lo}-{hi} total at WOT. Sanity bounds, not a target -- knock decides.")

    if profile and profile.block == "iron":
        extra.append("Iron block holds heat: timing that's clean on the first pull can "
                     "knock heat-soaked on the third. Watch IAT/ECT trend across pulls "
                     "and don't tune off a single cold run.")
        pull.append("Iron block + consecutive pulls -- expect knock to creep in as it heat-soaks.")
    elif profile and profile.block == "alum":
        extra.append("Aluminum sheds heat well, so heat-soak knock is less of a trap -- "
                     "your limit is more about compression and fuel than temperature.")

    if extra:
        advisory = advisory + "  " + "  ".join(extra)
    return advisory, pull
