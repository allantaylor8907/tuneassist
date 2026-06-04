"""
cams.py -- optional cam specs -> conservative tuning starting points.

A camshaft's duration @ .050 and lobe-separation angle (LSA) change how much
intake charge gets diluted by residuals at idle and low RPM (overlap/reversion),
which lowers dynamic compression and shifts where the engine makes torque. That
moves the *starting points* for idle airflow, idle timing, and low-RPM advance.

This is starting-point guidance ONLY (DESIGN.md S11). Nothing here is applied
without a knock-logged pull confirming it. All inputs optional; we degrade to
"stock-ish assumptions" when a field is missing.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CamSpec:
    """Numbers off a cam card. All optional."""
    intake_dur_050: float | None = None   # intake duration @ .050"
    exhaust_dur_050: float | None = None
    lsa: float | None = None              # lobe separation angle, deg
    lift: float | None = None             # max lift, inches (informational)

    @property
    def avg_dur_050(self) -> float | None:
        ds = [d for d in (self.intake_dur_050, self.exhaust_dur_050) if d]
        return sum(ds) / len(ds) if ds else None


def classify(cam: CamSpec) -> str:
    """stock / mild / big / unknown, from duration @ .050 and LSA.
    LS rule-of-thumb breakpoints: <210 stock-ish, 210-228 mild street, >228 big."""
    d = cam.avg_dur_050
    if d is None:
        return "unknown"
    if d < 210:
        klass = "stock"
    elif d <= 228:
        klass = "mild"
    else:
        klass = "big"
    # A tight LSA (< 112) adds overlap -> behaves "bigger" at idle.
    if cam.lsa is not None and cam.lsa < 112 and klass == "mild":
        klass = "big"
    return klass


@dataclass
class CamStartingPoints:
    klass: str
    idle_timing_deg: tuple            # (low, high) suggested idle advance
    idle_rpm: int                     # suggested target idle rpm
    notes: list


# "Easy" cam tiers -> a representative CamSpec (so classify/starting_points work).
CAM_TIERS = {
    "stock": CamSpec(intake_dur_050=196, exhaust_dur_050=201, lsa=115, lift=0.470),
    "mild":  CamSpec(intake_dur_050=219, exhaust_dur_050=225, lsa=113, lift=0.560),
    "race":  CamSpec(intake_dur_050=240, exhaust_dur_050=248, lsa=110, lift=0.630),
}


def tier_spec(tier: str) -> CamSpec | None:
    """Representative CamSpec for an 'easy' tier (stock/mild/race), else None."""
    return CAM_TIERS.get((tier or "").lower())


def starting_points(cam: CamSpec) -> CamStartingPoints:
    """Conservative starting numbers for a cammed LS, by class."""
    klass = classify(cam)
    # Idle-timing and airflow starting numbers per the Gen-3 LS playbook
    # (tuning101 / Goat Rope Garage): cammed idle ~800-900 RPM, idle spark mild
    # ~20-25 / aggressive ~28-30 deg, base running airflow ~+30-50% (x1.3-1.5).
    table = {
        "stock":   ((14, 18), 650, [
            "Stock-ish cam: factory idle/timing strategy is a fine baseline."]),
        "mild":    ((20, 25), 800, [
            "Mild cam: idle spark ~20-25 deg and idle target ~800 RPM is a good start.",
            "Raise base running airflow ~30% (x1.3) so it idles without the IAC pegged.",
            "Low-RPM/light-load can usually take a little more advance -- verify with knock."]),
        "big":     ((28, 30), 900, [
            "Big cam: idle spark ~28-30 deg (more advance smooths the lope), idle ~900 RPM.",
            "Raise base running airflow ~30-50% (x1.3-1.5) in the 600-1000 RPM range and open "
            "the idle bypass / 'percentage max' so the IAC has room.",
            "Expect empty low-RPM VE cells; torque peak is higher -- prescribe higher-RPM drives.",
            "Watch idle MAP -- it'll be higher/unsteadier than stock; don't chase it as a leak."]),
        "unknown": ((16, 22), 700, [
            "No cam specs given: assuming a mild street baseline. Enter duration @ .050 "
            "and LSA for sharper idle/timing starting points."]),
    }
    lo_hi, rpm, notes = table[klass]
    return CamStartingPoints(klass, lo_hi, rpm, list(notes))
