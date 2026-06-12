"""
fitment.py -- what combinations of platform / make / generation / engine are
actually REAL, so the UIs only ever offer possible choices.

The shape of the world this encodes:

  HP Tuners tunes factory ECUs, so the path is make -> generation -> engine
  (e.g. HP Tuners -> GM -> Gen 3 LS -> 6.0 LQ4). A carb'd SBC 350 is NOT an
  HP Tuners engine -- it has no factory ECU -- which is exactly why it lives
  under Holley instead.

  Holley replaces the ECU entirely, so the first question is WHICH Holley
  (Sniper, Terminator X, HP, Dominator); the engine itself can be nearly
  anything (classic carb-to-EFI or a modern swap). Engine details there don't
  change Holley's self-learn strategy, but they sharpen spark guidance and the
  garage record, so we still ask -- just per-make, flat (no generation tier).

One tree, consumed by the GUI (/api/presets fitment), and kept in sync with
profile.ENGINE_PRESETS (every engine label here must exist there -- enforced by
tests/test_fitment.py).
"""
from __future__ import annotations

# Generation (architecture) tiers for the HP Tuners platform, per make.
_HPT_GM = [
    {"key": "gm_gen3_ls", "label": "Gen 3 LS (1997-2007, 24x)",
     "engines": ["Chevy LS 4.8 (iron)", "Chevy LS 5.3 (iron truck)",
                 "Chevy LS 5.3 (aluminum)", "Chevy LS 6.0 LQ4 (iron)",
                 "Chevy LS 6.0 LQ9 (iron)", "Chevy LS1 5.7 (aluminum)",
                 "Chevy LS6 5.7 (aluminum)"]},
    {"key": "gm_gen4_ls", "label": "Gen 4 LS (2005-2017, 58x VVT)",
     "engines": ["Chevy LS2 6.0 (aluminum)", "Chevy LS3 6.2 (aluminum)",
                 "Chevy LS7 7.0 (aluminum)", "Chevy L92/L94 6.2 (aluminum)",
                 "Chevy LY6 6.0 (iron)", "Chevy LS 4.8/5.3 Gen 4 (iron)",
                 "Chevy LSA 6.2 supercharged", "Chevy LS9 6.2 supercharged"]},
    {"key": "gm_gen5_lt", "label": "Gen 5 LT (2014+, direct injection)",
     "engines": ["Chevy LT1 6.2 (aluminum)", "Chevy LT4 6.2 supercharged",
                 "Chevy L83 5.3 (aluminum)", "Chevy L86 6.2 (aluminum)",
                 "Chevy L8T 6.6 (iron)"]},
]
_HPT_FORD = [
    {"key": "ford_modular", "label": "Modular 4.6 / 5.4 (1996-2014)",
     "engines": ["Ford 4.6 modular 2V/3V", "Ford 5.4 modular"]},
    {"key": "ford_coyote", "label": "Coyote 5.0 (2011+)",
     "engines": ["Ford Coyote 5.0"]},
    {"key": "ford_godzilla", "label": "Godzilla 7.3 (2020+)",
     "engines": ["Ford Godzilla 7.3 (iron)"]},
]
_HPT_MOPAR = [
    {"key": "mopar_hemi", "label": "Gen 3 HEMI (2003+)",
     "engines": ["Mopar HEMI 5.7", "Mopar HEMI 6.1", "Mopar HEMI 6.4",
                 "Mopar Hellcat 6.2 supercharged"]},
]

# Holley engine lists per make: classics (carb-to-EFI) + common swaps. Flat --
# the Holley strategy doesn't branch on engine generation.
_HOLLEY_MAKES = [
    {"key": "gm", "label": "GM / Chevy",
     "engines": ["Chevy SBC 350 (iron)", "Chevy SBC 383 stroker",
                 "Chevy BBC 454 (iron)", "Chevy LS 4.8 (iron)",
                 "Chevy LS 5.3 (iron truck)", "Chevy LS 5.3 (aluminum)",
                 "Chevy LS 6.0 LQ4 (iron)", "Chevy LS 6.0 LQ9 (iron)",
                 "Chevy LS1 5.7 (aluminum)", "Chevy LS6 5.7 (aluminum)",
                 "Chevy LS2 6.0 (aluminum)", "Chevy LS3 6.2 (aluminum)",
                 "Chevy LS7 7.0 (aluminum)"]},
    {"key": "ford", "label": "Ford",
     "engines": ["Ford 5.0 / 302 (iron)", "Ford 347 stroker", "Ford 351W (iron)",
                 "Ford BBF 460 (iron)", "Ford 4.6 modular 2V/3V",
                 "Ford 5.4 modular", "Ford Coyote 5.0"]},
    {"key": "mopar", "label": "Mopar",
     "engines": ["Mopar 360 LA (iron)", "Mopar 440 (iron)", "Mopar HEMI 5.7",
                 "Mopar HEMI 6.1", "Mopar HEMI 6.4"]},
    {"key": "pontiac", "label": "Pontiac",
     "engines": ["Pontiac 400 (iron)", "Pontiac 455 (iron)"]},
    {"key": "other", "label": "Other", "engines": []},
]

# Holley product tiers (which box is on the engine). Doubles as the
# architecture value, so it persists and shows on the garage card.
HOLLEY_PRODUCTS = [
    {"key": "holley_sniper", "label": "Sniper / Sniper 2 (throttle body)"},
    {"key": "holley_terminator", "label": "Terminator X / X Max"},
    {"key": "holley_hp", "label": "HP EFI"},
    {"key": "holley_dominator", "label": "Dominator EFI"},
]

FITMENT = {
    "gm": {           # internal legacy key for the HP Tuners platform
        "label": "HP Tuners",
        "makes": [
            {"key": "gm", "label": "GM", "generations": _HPT_GM},
            {"key": "ford", "label": "Ford", "generations": _HPT_FORD},
            {"key": "mopar", "label": "Mopar / Dodge", "generations": _HPT_MOPAR},
        ],
    },
    "holley": {
        "label": "Holley EFI",
        "products": HOLLEY_PRODUCTS,
        "makes": _HOLLEY_MAKES,
    },
}


def makes_for(platform: str) -> list[dict]:
    return list(FITMENT.get(platform, {}).get("makes", []))


def generations_for(platform: str, make: str) -> list[dict]:
    """Generation tiers for a (platform, make). Holley has none (flat)."""
    for m in makes_for(platform):
        if m["key"] == make:
            return list(m.get("generations", []))
    return []


def engines_for(platform: str, make: str, generation: str | None = None) -> list[str]:
    """Engine preset labels valid for the selection. Holley ignores generation."""
    for m in makes_for(platform):
        if m["key"] != make:
            continue
        if "engines" in m:                       # flat (Holley)
            return list(m["engines"])
        for g in m.get("generations", []):
            if generation in (None, "", "auto") or g["key"] == generation:
                if g["key"] == generation:
                    return list(g["engines"])
        # generation unset: union of the make's engines, in tier order
        if generation in (None, "", "auto"):
            out: list[str] = []
            for g in m.get("generations", []):
                out.extend(e for e in g["engines"] if e not in out)
            return out
    return []


def infer_power_adder(engine_label: str | None, mods: list | None) -> str:
    """'boost' for factory-blown presets or boost mods; 'nitrous' for the bottle."""
    mods = mods or []
    if engine_label and "supercharged" in engine_label.lower():
        return "boost"
    if any(m.lower() in ("turbo", "supercharger") for m in mods):
        return "boost"
    if any(m.lower() == "nitrous" for m in mods):
        return "nitrous"
    return "na"
