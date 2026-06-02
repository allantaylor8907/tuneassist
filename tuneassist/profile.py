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
from dataclasses import dataclass


@dataclass
class EngineProfile:
    block: str | None = None          # 'iron' | 'alum'
    compression: float | None = None  # static CR, e.g. 9.5, 10.5
    displacement: float | None = None # liters, informational
    power_adder: str = "na"           # 'na' | 'boost' | 'nitrous'


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
